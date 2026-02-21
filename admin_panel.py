"""
Admin panel for managing settings and monitoring
"""
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session
from datetime import datetime
from sqlalchemy import func, or_, cast, String, asc, desc
from database import get_db, Setting, ProcessedTicket, OffloadLog, ZendeskTicketCache, ZendeskStorageSnapshot, TicketBackupItem, TicketBackupRun
from scheduler import OffloadScheduler
from offloader import AttachmentOffloader
from email_reporter import EmailReporter
from zendesk_client import ZendeskClient
from wasabi_client import WasabiClient
from config import (
    ADMIN_PANEL_PORT, ADMIN_PANEL_HOST, SECRET_KEY, ADMIN_USERNAME, ADMIN_PASSWORD,
    OAUTH_CLIENT_ID, OAUTH_REDIRECT_PATH, OAUTH_SCOPES, OAUTH_AUTHORITY
)
import os
import requests
from functools import wraps

app = Flask(__name__)
app.secret_key = SECRET_KEY

# Custom Jinja2 filters
import json as _json
@app.template_filter('fromjson')
def fromjson_filter(s):
    try:
        return _json.loads(s) if s else {}
    except Exception:
        return {}

# Global scheduler instance
scheduler = None
FORCED_PASSWORD_CHANGE_ALLOWED_ENDPOINTS = {'setup_admin_password', 'logout'}

# Error handler for API routes to return JSON instead of HTML
@app.errorhandler(404)
def not_found_error(error):
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'message': 'API endpoint not found'}), 404
    return error

@app.errorhandler(500)
def internal_error(error):
    if request.path.startswith('/api/'):
        import traceback
        import logging
        logger = logging.getLogger('zendesk_offloader')
        logger.error(f'API error: {str(error)}', exc_info=True)
        return jsonify({
            'success': False,
            'message': 'Internal server error. Please check the logs for details.'
        }), 500
    return error

# Configure logging to reduce noise from static file requests
import logging
from logging import Filter

class StaticFileFilter(Filter):
    """Filter out 304 responses for static files"""
    def filter(self, record):
        # Suppress 304 (Not Modified) responses for favicon and other static files
        msg = str(record.getMessage())
        msg_lower = msg.lower()
        
        # Suppress favicon requests (both 304 and 200)
        if 'favicon' in msg_lower:
            return False
        
        # Suppress 304 responses for static files
        if '304' in msg and ('/static/' in msg or 'static' in msg_lower):
            return False
            
        return True

# Apply filter to werkzeug logger and set level to WARNING to reduce noise
werkzeug_logger = logging.getLogger('werkzeug')
werkzeug_logger.setLevel(logging.WARNING)
werkzeug_logger.addFilter(StaticFileFilter())

# Module-level logger (used throughout this file)
logger = logging.getLogger('zendesk_offloader')

# ── Tenant context middleware ─────────────────────────────────────────────────
from flask import g as _flask_g

@app.before_request
def _inject_tenant_context():
    """
    For /t/<slug>/... routes, set g.tenant_slug so that get_db() automatically
    uses the correct per-tenant database.  Also attach the full TenantConfig.
    """
    from flask import g
    slug = None
    # URL-based tenant: /t/{slug}/...
    if request.path.startswith('/t/'):
        parts = request.path.split('/')
        if len(parts) >= 3 and parts[2]:
            slug = parts[2]
    if slug:
        try:
            from tenant_manager import get_tenant_config
            cfg = get_tenant_config(slug)
            if cfg:
                g.tenant_slug = slug
                g.tenant_cfg = cfg
                return
        except Exception:
            pass
    # Fallback: use first active tenant for legacy /dashboard, /tickets etc.
    if not request.path.startswith(('/login', '/logout', '/static', '/wizard',
                                    '/api/wizard', '/tenants', '/api/tenants')):
        try:
            from tenant_manager import list_tenants
            active = [t for t in list_tenants() if t.is_active]
            if active:
                g.tenant_slug = active[0].slug
                from tenant_manager import get_tenant_config
                g.tenant_cfg = get_tenant_config(active[0].slug)
        except Exception:
            pass
    # Always attach all tenants list for sidebar rendering
    try:
        from tenant_manager import list_tenants
        g.all_tenants = list_tenants(active_only=False)
    except Exception:
        g.all_tenants = []


def init_scheduler():
    """Initialize scheduler (singleton)"""
    global scheduler
    if scheduler is None:
        scheduler = OffloadScheduler()
    return scheduler


def _sanitize_for_json(obj):
    """Recursively convert datetime objects to ISO strings for safe JSON serialization."""
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(item) for item in obj]
    elif isinstance(obj, datetime):
        return obj.isoformat()
    return obj

def login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        # Check if user is logged in via OAuth or password
        if not session.get('logged_in') and not session.get('user_email'):
            # For API endpoints, return JSON error instead of redirecting
            if request.path.startswith('/api/'):
                return jsonify({
                    'success': False,
                    'message': 'Authentication required. Please log in.'
                }), 401
            return redirect(url_for('login', next=request.path))
        if session.get('must_change_password'):
            endpoint = request.endpoint or ''
            if endpoint not in FORCED_PASSWORD_CHANGE_ALLOWED_ENDPOINTS:
                if request.path.startswith('/api/'):
                    return jsonify({
                        'success': False,
                        'message': 'Admin password setup required. Please create a new password.'
                    }), 403
                return redirect(url_for('setup_admin_password', next=request.path))
        return view_func(*args, **kwargs)
    return wrapper

def _is_admin_password_configured():
    """Check if admin password exists in database settings."""
    db = get_db()
    try:
        setting = db.query(Setting).filter_by(key='ADMIN_PASSWORD').first()
        return bool(setting and setting.value)
    finally:
        db.close()

@app.route('/favicon.ico')
def favicon():
    """Serve favicon with proper headers and cache control"""
    from flask import send_from_directory, make_response
    import os
    
    response = make_response(send_from_directory(
        os.path.join(app.root_path, 'static'),
        'favicon.ico',
        mimetype='image/vnd.microsoft.icon'
    ))
    
    # Set cache headers to reduce requests
    response.cache_control.max_age = 86400  # 1 day
    response.cache_control.public = True
    
    return response

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Admin login - supports both OAuth and password authentication"""
    # If already logged in, go to dashboard
    if session.get('logged_in') or session.get('user_email'):
        return redirect(url_for('index'))
    
    # Handle password login (fallback)
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')  # Don't strip password - preserve exact value
        
        # Get admin credentials - check database first, then config
        db = get_db()
        expected_username = ADMIN_USERNAME
        expected_password = ADMIN_PASSWORD
        password_from_db = False
        try:
            admin_username_setting = db.query(Setting).filter_by(key='ADMIN_USERNAME').first()
            admin_password_setting = db.query(Setting).filter_by(key='ADMIN_PASSWORD').first()
            
            # Use database value if exists, otherwise use config default
            if admin_username_setting and admin_username_setting.value:
                expected_username = admin_username_setting.value.strip()
            elif ADMIN_USERNAME:
                expected_username = ADMIN_USERNAME.strip()
            
            if admin_password_setting and admin_password_setting.value:
                expected_password = admin_password_setting.value  # Don't strip password
                password_from_db = True
            elif ADMIN_PASSWORD:
                expected_password = ADMIN_PASSWORD  # Don't strip password
        except Exception as e:
            import logging
            logger = logging.getLogger('zendesk_offloader')
            logger.error(f"Error retrieving credentials from database: {e}", exc_info=True)
            # Fall back to config defaults
            expected_username = ADMIN_USERNAME.strip() if ADMIN_USERNAME else "admin"
            expected_password = ADMIN_PASSWORD if ADMIN_PASSWORD else ""
        finally:
            db.close()
        
        # Compare credentials
        username_match = username == expected_username
        password_match = password == expected_password
        
        # Debug logging for troubleshooting
        import logging
        logger = logging.getLogger('zendesk_offloader')
        if not username_match or not password_match:
            # Log detailed comparison info
            logger.warning(f"Login failed:")
            logger.warning(f"  Username match: {username_match}")
            logger.warning(f"  Password match: {password_match}")
            logger.warning(f"  Username provided: '{username}' (len={len(username)}, bytes={username.encode('utf-8')})")
            logger.warning(f"  Username expected: '{expected_username}' (len={len(expected_username)}, bytes={expected_username.encode('utf-8')})")
            logger.warning(f"  Password provided: length={len(password)}, bytes={password.encode('utf-8')[:20]}...")
            logger.warning(f"  Password expected: length={len(expected_password)}, bytes={expected_password.encode('utf-8')[:20]}...")
            
            # Check if passwords are similar (first few chars)
            if len(password) > 0 and len(expected_password) > 0:
                logger.warning(f"  Password first 5 chars - provided: '{password[:5]}', expected: '{expected_password[:5]}'")
                logger.warning(f"  Password last 5 chars - provided: '{password[-5:]}', expected: '{expected_password[-5:]}'")
        
        if username_match and password_match:
            session['logged_in'] = True
            session['username'] = username
            session['must_change_password'] = not password_from_db
            if session['must_change_password']:
                flash('Create a new admin password to finish setup.', 'warning')
                return redirect(url_for('setup_admin_password', next=request.args.get('next')))
            next_url = request.args.get('next') or url_for('index')
            return redirect(next_url)
        flash('Invalid credentials', 'error')
        return render_template('login.html', oauth_enabled=bool(OAUTH_CLIENT_ID))
    
    return render_template('login.html', oauth_enabled=bool(OAUTH_CLIENT_ID))

@app.route('/setup/admin_password', methods=['GET', 'POST'])
@login_required
def setup_admin_password():
    """Force admin to create a password stored in the database."""
    password_already_configured = _is_admin_password_configured()
    if not session.get('must_change_password') and password_already_configured:
        flash('Admin password is already configured.', 'info')
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')
        next_url = request.form.get('next') or url_for('index')
        
        if len(new_password) < 12:
            flash('Password must be at least 12 characters long.', 'error')
            return render_template('setup_admin_password.html', next_url=request.form.get('next'))
        
        if new_password != confirm_password:
            flash('Passwords do not match.', 'error')
            return render_template('setup_admin_password.html', next_url=request.form.get('next'))
        
        _save_admin_password(new_password)
        session['must_change_password'] = False
        flash('Admin password updated successfully.', 'success')
        return redirect(next_url)
    
    return render_template('setup_admin_password.html', next_url=request.args.get('next'))

@app.route('/login/oauth')
def login_oauth():
    """Initiate OAuth login"""
    if not OAUTH_CLIENT_ID:
        flash('OAuth is not configured', 'error')
        return redirect(url_for('login'))
    
    try:
        from oauth_auth import _build_auth_code_flow
        
        # Store next URL if provided
        next_url = request.args.get('next')
        if next_url:
            session['next_url'] = next_url
        
        # Build redirect URI
        redirect_uri = request.url_root.rstrip('/') + OAUTH_REDIRECT_PATH
        
        flow = _build_auth_code_flow(
            redirect_uri=redirect_uri
        )
        session["flow"] = flow
        return redirect(flow["auth_uri"])
    except Exception as e:
        import logging
        logger = logging.getLogger('zendesk_offloader')
        logger.error(f'OAuth login initiation error: {str(e)}', exc_info=True)
        flash(f'OAuth login failed: {str(e)}', 'error')
        return redirect(url_for('login'))

@app.route(OAUTH_REDIRECT_PATH)
def authorized():
    """OAuth callback handler"""
    if not OAUTH_CLIENT_ID:
        flash('OAuth is not configured', 'error')
        return redirect(url_for('login'))
    
    try:
        from oauth_auth import _build_msal_app, _load_cache, _save_cache, validate_user_domain
        
        # Check for errors in the callback
        if "error" in request.args:
            error_description = request.args.get("error_description", "Unknown error")
            flash(f'OAuth error: {error_description}', 'error')
            return redirect(url_for('login'))
        
        cache = _load_cache()
        result = None
        
        # Get the flow from session
        flow = session.get("flow", {})
        if not flow:
            flash('OAuth session expired. Please try again.', 'error')
            return redirect(url_for('login'))
        
        if "code" in request.args:
            # Build the app with the same authority
            app = _build_msal_app(cache=cache, authority=OAUTH_AUTHORITY)
            result = app.acquire_token_by_auth_code_flow(flow, request.args)
            _save_cache(cache)
        
        # Clear flow from session
        session.pop("flow", None)
        
        if result and "error" in result:
            flash(f'OAuth error: {result.get("error_description", "Unknown error")}', 'error')
            return redirect(url_for('login'))
        
        if result and "access_token" in result:
            # Get user info from Microsoft Graph
            graph_response = requests.get(
                "https://graph.microsoft.com/v1.0/me",
                headers={'Authorization': 'Bearer ' + result['access_token']},
                timeout=10
            )
            
            if graph_response.status_code != 200:
                flash('Failed to retrieve user information from Microsoft', 'error')
                return redirect(url_for('login'))
            
            graph_data = graph_response.json()
            user_email = graph_data.get('mail') or graph_data.get('userPrincipalName', '')
            user_name = graph_data.get('displayName', '')
            
            if not user_email:
                flash('Unable to retrieve email from Microsoft account', 'error')
                return redirect(url_for('login'))
            
            # Validate domain
            try:
                validate_user_domain(user_email)
            except ValueError as e:
                flash(str(e), 'error')
                session.clear()
                return redirect(url_for('login'))
            
            # Set session
            session['user_email'] = user_email
            session['user_name'] = user_name
            session['logged_in'] = True
            session['username'] = user_name or user_email
            session['must_change_password'] = not _is_admin_password_configured()
            
            if session['must_change_password']:
                flash('Create a new admin password to finish setup.', 'warning')
                next_url = request.args.get('next') or session.pop('next_url', None)
                return redirect(url_for('setup_admin_password', next=next_url))
            
            next_url = request.args.get('next') or session.pop('next_url', None) or url_for('index')
            return redirect(next_url)
        else:
            flash('Authentication failed. No access token received.', 'error')
            return redirect(url_for('login'))
            
    except Exception as e:
        import logging
        logger = logging.getLogger('zendesk_offloader')
        logger.error(f'OAuth authentication error: {str(e)}', exc_info=True)
        flash(f'OAuth authentication error: {str(e)}', 'error')
        session.pop("flow", None)
        return redirect(url_for('login'))

@app.route('/logout')
def logout():
    """End session - handles both OAuth and password login"""
    # Clear OAuth token cache if exists
    if session.get("token_cache"):
        session.pop("token_cache")
    
    # Clear all session data
    session.clear()
    flash('Logged out', 'success')
    return redirect(url_for('login'))


# ══════════════════════════════════════════════════════════════════════════════
# GLOBAL TENANTS OVERVIEW  —  /tenants
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/tenants')
@login_required
def tenants_overview():
    """Global tenant management page."""
    from tenant_manager import list_tenants, get_tenant_config, get_tenant_db_session
    from sqlalchemy import func as sqlfunc

    tenant_rows = list_tenants()
    cards = []
    for t in tenant_rows:
        cfg = get_tenant_config(t.slug)
        card = {
            'slug': t.slug,
            'display_name': t.display_name or t.slug,
            'is_active': t.is_active,
            'created_at': t.created_at,
            'configured': cfg.is_configured if cfg else False,
            'tickets_processed': 0,
            'tickets_backed_up': 0,
            'last_offload': None,
            'last_backup': None,
            'errors_today': 0,
            'storage_bytes': 0,
            'red_flags': [],
        }
        # Pull stats from per-tenant DB (best-effort)
        try:
            tdb = get_tenant_db_session(t.slug)
            from database import ProcessedTicket, OffloadLog, TicketBackupItem, TicketBackupRun
            card['tickets_processed'] = tdb.query(sqlfunc.count(ProcessedTicket.id)).scalar() or 0
            card['tickets_backed_up'] = tdb.query(sqlfunc.count(TicketBackupItem.id))\
                .filter(TicketBackupItem.backup_status == 'success').scalar() or 0
            last_log = tdb.query(OffloadLog).order_by(OffloadLog.run_date.desc()).first()
            if last_log:
                card['last_offload'] = last_log.run_date
                card['errors_today'] = last_log.errors_count or 0
            last_bak = tdb.query(TicketBackupRun).order_by(TicketBackupRun.run_date.desc()).first()
            if last_bak:
                card['last_backup'] = last_bak.run_date
            # Red flags
            from datetime import timedelta
            now = datetime.utcnow()
            if last_log and (now - last_log.run_date) > timedelta(hours=2):
                card['red_flags'].append('No offload in 2h+')
            failed_today = tdb.query(sqlfunc.count(TicketBackupItem.id))\
                .filter(TicketBackupItem.backup_status == 'failed').scalar() or 0
            if failed_today > 0:
                card['red_flags'].append(f'{failed_today} backup failures')
            if card['errors_today'] > 5:
                card['red_flags'].append(f'{card["errors_today"]} offload errors')
            tdb.close()
        except Exception:
            pass
        cards.append(card)

    return render_template('tenants.html', cards=cards)


@app.route('/api/tenants/<slug>/toggle', methods=['POST'])
@login_required
def tenant_toggle(slug):
    from tenant_manager import get_global_db, Tenant
    gdb = get_global_db()
    try:
        t = gdb.query(Tenant).filter_by(slug=slug).first()
        if not t:
            return jsonify({'success': False, 'message': 'Tenant not found'}), 404
        t.is_active = not t.is_active
        gdb.commit()
        return jsonify({'success': True, 'is_active': t.is_active})
    finally:
        gdb.close()


@app.route('/api/tenants/<slug>/delete', methods=['POST'])
@login_required
def tenant_delete(slug):
    from tenant_manager import delete_tenant
    ok = delete_tenant(slug, remove_data=False)  # soft-delete
    return jsonify({'success': ok})


# ══════════════════════════════════════════════════════════════════════════════
# ADD-TENANT WIZARD  —  /wizard
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/wizard')
@login_required
def wizard():
    """New tenant setup wizard."""
    return render_template('wizard.html')


@app.route('/api/wizard/test_zendesk', methods=['POST'])
@login_required
def wizard_test_zendesk():
    """Step 1 — validate Zendesk credentials."""
    data = request.get_json(force=True) or {}
    subdomain = (data.get('subdomain') or '').strip().lower()
    email = (data.get('email') or '').strip()
    api_token = (data.get('api_token') or '').strip()
    if not subdomain or not email or not api_token:
        return jsonify({'success': False, 'message': 'All fields are required'})
    try:
        import requests as req
        url = f'https://{subdomain}.zendesk.com/api/v2/tickets/count.json'
        r = req.get(url, auth=(f'{email}/token', api_token), timeout=10)
        if r.status_code == 200:
            count = r.json().get('count', {}).get('value', '?')
            return jsonify({'success': True, 'message': f'Connected ✓ — {count} total tickets'})
        elif r.status_code == 401:
            return jsonify({'success': False, 'message': 'Invalid credentials (401)'})
        else:
            return jsonify({'success': False, 'message': f'Zendesk returned HTTP {r.status_code}'})
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)})


@app.route('/api/wizard/test_wasabi', methods=['POST'])
@login_required
def wizard_test_wasabi():
    """Step 2 — validate Wasabi bucket access."""
    data = request.get_json(force=True) or {}
    endpoint = (data.get('endpoint') or '').strip()
    access_key = (data.get('access_key') or '').strip()
    secret_key = (data.get('secret_key') or '').strip()
    bucket = (data.get('bucket') or '').strip()
    if not all([endpoint, access_key, secret_key, bucket]):
        return jsonify({'success': False, 'message': 'All fields are required'})
    try:
        import boto3
        ep = endpoint if endpoint.startswith('http') else f'https://{endpoint}'
        s3 = boto3.client('s3', endpoint_url=ep,
                          aws_access_key_id=access_key, aws_secret_access_key=secret_key)
        s3.head_bucket(Bucket=bucket)
        return jsonify({'success': True, 'message': f'Bucket "{bucket}" accessible ✓'})
    except Exception as exc:
        msg = str(exc)
        if 'NoSuchBucket' in msg or '404' in msg:
            # Try to create it
            try:
                s3.create_bucket(Bucket=bucket)
                return jsonify({'success': True, 'message': f'Bucket "{bucket}" created ✓'})
            except Exception as exc2:
                return jsonify({'success': False, 'message': f'Bucket not found and could not create: {exc2}'})
        return jsonify({'success': False, 'message': msg})


@app.route('/api/wizard/test_offload', methods=['POST'])
@login_required
def wizard_test_offload():
    """Step 3 — send a single-ticket offload test."""
    data = request.get_json(force=True) or {}
    ticket_id = data.get('ticket_id')
    if not ticket_id:
        return jsonify({'success': False, 'message': 'ticket_id required'})
    try:
        from tenant_manager import TenantConfig
        cfg = TenantConfig(
            slug='__wizard_test__',
            zendesk_subdomain=data.get('zendesk_subdomain', ''),
            zendesk_email=data.get('zendesk_email', ''),
            zendesk_api_token=data.get('zendesk_api_token', ''),
            wasabi_endpoint=data.get('wasabi_endpoint', ''),
            wasabi_access_key=data.get('wasabi_access_key', ''),
            wasabi_secret_key=data.get('wasabi_secret_key', ''),
            wasabi_bucket_name=data.get('wasabi_bucket', ''),
        )
        from zendesk_client import ZendeskClient
        from wasabi_client import WasabiClient
        zd = ZendeskClient(subdomain=cfg.zendesk_subdomain,
                           email=cfg.zendesk_email,
                           api_token=cfg.zendesk_api_token)
        attachments = zd.get_ticket_attachments(int(ticket_id))
        if not attachments:
            return jsonify({'success': True, 'message': 'Ticket has no attachments — credentials OK ✓'})
        ws = WasabiClient(endpoint=cfg.wasabi_endpoint, access_key=cfg.wasabi_access_key,
                          secret_key=cfg.wasabi_secret_key, bucket_name=cfg.wasabi_bucket_name)
        uploaded = []
        for att in attachments[:2]:  # test first 2 only
            key = f'__wizard_test__/{ticket_id}/{att["file_name"]}'
            # Download the attachment bytes then upload to Wasabi
            import urllib.request as _urlreq
            import base64 as _b64
            req = _urlreq.Request(att['content_url'])
            creds = _b64.b64encode(
                f'{cfg.zendesk_email}/token:{cfg.zendesk_api_token}'.encode()
            ).decode()
            req.add_header('Authorization', f'Basic {creds}')
            with _urlreq.urlopen(req, timeout=30) as resp:
                raw = resp.read()
            ws.s3_client.put_object(
                Bucket=ws.bucket_name,
                Key=key,
                Body=raw,
                ContentType=att.get('content_type', 'application/octet-stream'),
            )
            # Clean up test file
            try:
                ws.s3_client.delete_object(Bucket=ws.bucket_name, Key=key)
            except Exception:
                pass
            uploaded.append(att['file_name'])
        return jsonify({'success': True, 'message': f'Uploaded {len(uploaded)} attachment(s) ✓: {", ".join(uploaded)}'})
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)})


@app.route('/api/wizard/test_backup', methods=['POST'])
@login_required
def wizard_test_backup():
    """Step 4 — test ticket backup bucket."""
    data = request.get_json(force=True) or {}
    endpoint = (data.get('backup_endpoint') or '').strip()
    access_key = (data.get('wasabi_access_key') or '').strip()
    secret_key = (data.get('wasabi_secret_key') or '').strip()
    bucket = (data.get('backup_bucket') or '').strip()
    if not all([endpoint, access_key, secret_key, bucket]):
        return jsonify({'success': False, 'message': 'Backup bucket fields required'})
    try:
        import boto3, json as _json
        ep = endpoint if endpoint.startswith('http') else f'https://{endpoint}'
        s3 = boto3.client('s3', endpoint_url=ep,
                          aws_access_key_id=access_key, aws_secret_access_key=secret_key)
        try:
            s3.head_bucket(Bucket=bucket)
        except Exception:
            s3.create_bucket(Bucket=bucket)
        test_key = '__wizard_backup_test__.json'
        s3.put_object(Bucket=bucket, Key=test_key,
                      Body=_json.dumps({'test': True, 'ts': datetime.utcnow().isoformat()}))
        s3.delete_object(Bucket=bucket, Key=test_key)
        return jsonify({'success': True, 'message': f'Backup bucket "{bucket}" write/delete OK ✓'})
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)})


@app.route('/api/wizard/test_notifications', methods=['POST'])
@login_required
def wizard_test_notifications():
    """Step 5 — test Telegram + Slack."""
    data = request.get_json(force=True) or {}
    results = {}
    # Telegram
    tg_token = (data.get('telegram_bot_token') or '').strip()
    tg_chat  = (data.get('telegram_chat_id') or '').strip()
    if tg_token and tg_chat:
        try:
            from telegram_reporter import TelegramReporter
            rep = TelegramReporter(bot_token=tg_token, chat_id=tg_chat)
            ok = rep.send_simple_message('🟢 z2w wizard test — Telegram OK')
            results['telegram'] = 'OK ✓' if ok else 'send failed'
        except Exception as exc:
            results['telegram'] = str(exc)
    else:
        results['telegram'] = 'skipped (not configured)'

    # Slack
    slack_url = (data.get('slack_webhook_url') or '').strip()
    if slack_url:
        try:
            import requests as req
            r = req.post(slack_url, json={'text': '🟢 z2w wizard test — Slack OK'}, timeout=8)
            results['slack'] = 'OK ✓' if r.ok else f'HTTP {r.status_code}'
        except Exception as exc:
            results['slack'] = str(exc)
    else:
        results['slack'] = 'skipped (not configured)'

    return jsonify({'success': True, 'results': results})


@app.route('/api/wizard/save', methods=['POST'])
@login_required
def wizard_save():
    """Save a new tenant from wizard data."""
    data = request.get_json(force=True) or {}
    subdomain = (data.get('zendesk_subdomain') or '').strip().lower()
    if not subdomain:
        return jsonify({'success': False, 'message': 'zendesk_subdomain required'})
    import re as _re
    slug = _re.sub(r'[^a-z0-9\-]', '-', subdomain).strip('-') or 'default'
    from tenant_manager import TenantConfig, save_tenant_config, get_tenant_config
    existing = get_tenant_config(slug)
    if existing and not data.get('overwrite'):
        return jsonify({'success': False, 'message': f'Tenant "{slug}" already exists',
                        'exists': True, 'slug': slug})
    cfg = TenantConfig(
        slug=slug,
        display_name=data.get('display_name') or subdomain,
        zendesk_subdomain=subdomain,
        zendesk_email=data.get('zendesk_email', ''),
        zendesk_api_token=data.get('zendesk_api_token', ''),
        wasabi_endpoint=data.get('wasabi_endpoint', ''),
        wasabi_access_key=data.get('wasabi_access_key', ''),
        wasabi_secret_key=data.get('wasabi_secret_key', ''),
        wasabi_bucket_name=data.get('wasabi_bucket', ''),
        ticket_backup_endpoint=data.get('backup_endpoint', ''),
        ticket_backup_bucket=data.get('backup_bucket', ''),
        telegram_bot_token=data.get('telegram_bot_token', ''),
        telegram_chat_id=data.get('telegram_chat_id', ''),
        slack_webhook_url=data.get('slack_webhook_url', ''),
    )
    save_tenant_config(cfg)
    return jsonify({'success': True, 'slug': slug,
                    'message': f'Tenant "{slug}" created successfully'})


# ── Per-tenant route aliases (/t/{slug}/... → existing view functions) ────────

@app.route('/t/<slug>/')
@app.route('/t/<slug>/dashboard')
@login_required
def tenant_dashboard(slug):
    from flask import g
    g.tenant_slug = slug
    from tenant_manager import get_tenant_config
    g.tenant_cfg = get_tenant_config(slug)
    return index()


@app.route('/t/<slug>/tickets')
@login_required
def tenant_tickets(slug):
    from flask import g
    g.tenant_slug = slug
    from tenant_manager import get_tenant_config
    g.tenant_cfg = get_tenant_config(slug)
    return tickets()


@app.route('/t/<slug>/ticket_backup')
@login_required
def tenant_ticket_backup(slug):
    from flask import g
    g.tenant_slug = slug
    from tenant_manager import get_tenant_config
    g.tenant_cfg = get_tenant_config(slug)
    return ticket_backup()


@app.route('/t/<slug>/storage')
@login_required
def tenant_storage(slug):
    from flask import g
    g.tenant_slug = slug
    from tenant_manager import get_tenant_config
    g.tenant_cfg = get_tenant_config(slug)
    return storage_report()


@app.route('/t/<slug>/explorer')
@login_required
def tenant_explorer(slug):
    from flask import g
    g.tenant_slug = slug
    from tenant_manager import get_tenant_config
    g.tenant_cfg = get_tenant_config(slug)
    return explorer_app()


@app.route('/t/<slug>/logs')
@login_required
def tenant_logs(slug):
    from flask import g
    g.tenant_slug = slug
    from tenant_manager import get_tenant_config
    g.tenant_cfg = get_tenant_config(slug)
    return logs()


@app.route('/t/<slug>/settings', methods=['GET', 'POST'])
@login_required
def tenant_settings(slug):
    """Per-tenant settings — read/write via global.db TenantSetting."""
    from flask import g
    from tenant_manager import get_tenant_config, save_tenant_config, TenantConfig
    cfg = get_tenant_config(slug)
    if not cfg:
        return f'Tenant "{slug}" not found', 404
    g.tenant_slug = slug
    g.tenant_cfg = cfg

    if request.method == 'POST':
        d = request.form
        for field_name in ['display_name', 'zendesk_subdomain', 'zendesk_email',
                           'zendesk_api_token', 'wasabi_endpoint', 'wasabi_access_key',
                           'wasabi_secret_key', 'wasabi_bucket_name',
                           'ticket_backup_endpoint', 'ticket_backup_bucket',
                           'telegram_bot_token', 'telegram_chat_id',
                           'slack_webhook_url', 'slack_bot_token',
                           'scheduler_timezone', 'ticket_backup_time']:
            if field_name in d:
                setattr(cfg, field_name, d[field_name])
        for int_field in ['continuous_offload_interval', 'attach_offload_interval_minutes',
                          'ticket_backup_interval_minutes', 'ticket_backup_max_per_run',
                          'max_attachments_per_run', 'storage_report_interval']:
            if int_field in d:
                try:
                    setattr(cfg, int_field, int(d[int_field]))
                except ValueError:
                    pass
        for bool_field in ['attach_offload_enabled', 'ticket_backup_enabled']:
            setattr(cfg, bool_field, bool_field in d)
        save_tenant_config(cfg)
        flash('Settings saved', 'success')
        return redirect(url_for('tenant_settings', slug=slug))

    return render_template('tenant_settings.html', cfg=cfg, slug=slug)


# ══════════════════════════════════════════════════════════════════════════════
# BUCKET BROWSER  —  /t/<slug>/bucket
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/t/<slug>/bucket')
@login_required
def tenant_bucket_browser(slug):
    """Storage / bucket browser page for a tenant."""
    from flask import g
    from tenant_manager import get_tenant_config
    cfg = get_tenant_config(slug)
    if not cfg:
        return f'Tenant "{slug}" not found', 404
    g.tenant_slug = slug
    g.tenant_cfg = cfg

    # Determine which buckets are configured
    has_offload = bool(cfg.wasabi_access_key and cfg.wasabi_secret_key and cfg.wasabi_bucket_name)
    has_backup  = bool(cfg.ticket_backup_endpoint and cfg.ticket_backup_bucket and
                       cfg.wasabi_access_key and cfg.wasabi_secret_key)

    return render_template(
        'bucket_browser.html',
        cfg=cfg,
        slug=slug,
        has_offload=has_offload,
        has_backup=has_backup,
    )


@app.route('/api/t/<slug>/bucket/list')
@login_required
def api_bucket_list(slug):
    """
    JSON: list folders + files at a given prefix.

    Query params:
        prefix      — S3 prefix (folder path), default ''
        bucket_type — 'offload' or 'backup', default 'offload'
    """
    from tenant_manager import get_tenant_config
    from wasabi_client import WasabiClient

    cfg = get_tenant_config(slug)
    if not cfg:
        return jsonify({'error': 'Tenant not found'}), 404

    prefix      = request.args.get('prefix', '')
    bucket_type = request.args.get('bucket_type', 'offload')

    try:
        if bucket_type == 'backup':
            endpoint    = cfg.ticket_backup_endpoint or cfg.wasabi_endpoint
            bucket_name = cfg.ticket_backup_bucket
        else:
            endpoint    = cfg.wasabi_endpoint
            bucket_name = cfg.wasabi_bucket_name

        if not bucket_name:
            return jsonify({'error': 'Bucket not configured', 'folders': [], 'files': []}), 200

        ws = WasabiClient(
            endpoint    = endpoint,
            access_key  = cfg.wasabi_access_key,
            secret_key  = cfg.wasabi_secret_key,
            bucket_name = bucket_name,
        )
        result = ws.list_objects(prefix=prefix)

        # Serialise datetimes for JSON
        for f in result['files']:
            if f['last_modified']:
                f['last_modified'] = f['last_modified'].strftime('%Y-%m-%d %H:%M UTC')

        result['prefix']      = prefix
        result['bucket_name'] = bucket_name
        result['bucket_type'] = bucket_type
        return jsonify(result)

    except Exception as exc:
        logger.exception('bucket list error for %s', slug)
        return jsonify({'error': str(exc), 'folders': [], 'files': []}), 200


@app.route('/api/t/<slug>/bucket/presign')
@login_required
def api_bucket_presign(slug):
    """
    Return a short-lived presigned URL for a given key.

    Query params:
        key         — S3 key
        bucket_type — 'offload' or 'backup'
    """
    from tenant_manager import get_tenant_config
    from wasabi_client import WasabiClient

    cfg = get_tenant_config(slug)
    if not cfg:
        return jsonify({'error': 'Tenant not found'}), 404

    key         = request.args.get('key', '').strip()
    bucket_type = request.args.get('bucket_type', 'offload')

    if not key:
        return jsonify({'error': 'No key provided'}), 400

    try:
        if bucket_type == 'backup':
            endpoint    = cfg.ticket_backup_endpoint or cfg.wasabi_endpoint
            bucket_name = cfg.ticket_backup_bucket
        else:
            endpoint    = cfg.wasabi_endpoint
            bucket_name = cfg.wasabi_bucket_name

        ws = WasabiClient(
            endpoint    = endpoint,
            access_key  = cfg.wasabi_access_key,
            secret_key  = cfg.wasabi_secret_key,
            bucket_name = bucket_name,
        )
        url = ws.presign_url(key, expires_in=3600)
        return jsonify({'url': url})

    except Exception as exc:
        logger.exception('presign error for %s key=%s', slug, key)
        return jsonify({'error': str(exc)}), 500


@app.route('/')
@app.route('/dashboard')
@login_required
def index():
    """Dashboard"""
    db = get_db()
    try:
        # Get statistics
        total_processed = db.query(ProcessedTicket).count()
        total_attachments = db.query(ProcessedTicket).filter(
            ProcessedTicket.status == 'processed'
        ).with_entities(
            func.sum(ProcessedTicket.attachments_count)
        ).scalar() or 0

        # Ticket cache stats
        cache_total = db.query(ZendeskTicketCache).count()
        from sqlalchemy import func as sqlfunc
        cache_last_sync = db.query(sqlfunc.max(ZendeskTicketCache.cached_at)).scalar()

        recent_logs = db.query(OffloadLog).order_by(
            OffloadLog.run_date.desc()
        ).all()

        # Get scheduler status
        sched = init_scheduler()
        next_run = None
        if sched.scheduler.running:
            jobs = sched.scheduler.get_jobs()
            if jobs:
                next_run = jobs[0].next_run_time

        return render_template('dashboard.html',
                             total_processed=total_processed,
                             total_attachments=total_attachments,
                             recent_logs=recent_logs,
                             next_run=next_run,
                             cache_total=cache_total,
                             cache_last_sync=cache_last_sync)
    finally:
        db.close()

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    """Settings page"""
    db = get_db()
    try:
        if request.method == 'POST':
            from config import BASE_DIR
            
            # Read existing .env file if it exists
            env_file = BASE_DIR / '.env'
            env_lines = {}
            if env_file.exists():
                with open(env_file, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key = line.split('=', 1)[0].strip()
                            env_lines[key] = line
            
            # Update settings in database and prepare .env updates
            env_updates = {}
            scheduler_settings_changed = False
            scheduler_keys = {'SCHEDULER_TIMEZONE', 'SCHEDULER_HOUR', 'SCHEDULER_MINUTE',
                              'RECHECK_HOUR', 'CONTINUOUS_OFFLOAD_INTERVAL', 'STORAGE_REPORT_INTERVAL'}
            
            for key, value in request.form.items():
                # Check if scheduler settings changed
                if key in scheduler_keys:
                    old_setting = db.query(Setting).filter_by(key=key).first()
                    if old_setting and old_setting.value != value:
                        scheduler_settings_changed = True
                    elif not old_setting:
                        scheduler_settings_changed = True
                
                # Update database
                setting = db.query(Setting).filter_by(key=key).first()
                if setting:
                    setting.value = value
                    setting.updated_at = datetime.utcnow()
                else:
                    setting = Setting(key=key, value=value)
                    db.add(setting)
                
                # Prepare .env update
                env_updates[key] = value
            
            db.commit()
            
            # Write to .env file
            try:
                # Read all existing lines
                existing_lines = []
                if env_file.exists():
                    with open(env_file, 'r') as f:
                        existing_lines = f.readlines()
                
                # Create new content
                new_lines = []
                updated_keys = set()
                
                # Process existing lines, updating values
                for line in existing_lines:
                    stripped = line.strip()
                    if stripped and not stripped.startswith('#') and '=' in stripped:
                        key = stripped.split('=', 1)[0].strip()
                        if key in env_updates:
                            new_lines.append(f"{key}={env_updates[key]}\n")
                            updated_keys.add(key)
                        else:
                            new_lines.append(line)
                    else:
                        new_lines.append(line)
                
                # Add new keys that weren't in the file
                for key, value in env_updates.items():
                    if key not in updated_keys:
                        new_lines.append(f"{key}={value}\n")
                
                # Write back to file
                with open(env_file, 'w') as f:
                    f.writelines(new_lines)
                
                # Reload config
                from config import reload_config
                reload_config()
                
                # If scheduler settings changed, restart scheduler
                if scheduler_settings_changed:
                    try:
                        sched = init_scheduler()
                        was_running = sched.scheduler.running
                        if was_running:
                            sched.stop()
                            import time
                            time.sleep(1)
                        
                        # Reinitialize scheduler with new settings
                        global scheduler
                        scheduler = None
                        sched = init_scheduler()
                        
                        if was_running:
                            sched.start()
                            flash('Settings updated successfully. Scheduler restarted with new settings.', 'success')
                        else:
                            flash('Settings updated successfully. Scheduler settings saved (scheduler was not running).', 'success')
                    except Exception as e:
                        flash(f'Settings saved but failed to restart scheduler: {str(e)}', 'warning')
                else:
                    flash('Settings updated successfully', 'success')
                
            except Exception as e:
                flash(f'Settings saved to database but failed to update .env file: {str(e)}', 'warning')
            
            return redirect(url_for('settings'))
        
        # Get all settings from database
        settings_list = db.query(Setting).all()
        settings_dict = {s.key: s.value for s in settings_list}
        
        # Get environment variables as defaults (only if not in database)
        env_settings = {
            'ZENDESK_SUBDOMAIN': os.getenv('ZENDESK_SUBDOMAIN', ''),
            'ZENDESK_EMAIL': os.getenv('ZENDESK_EMAIL', ''),
            'ZENDESK_API_TOKEN': os.getenv('ZENDESK_API_TOKEN', ''),
            'WASABI_ENDPOINT': os.getenv('WASABI_ENDPOINT', ''),
            'WASABI_ACCESS_KEY': os.getenv('WASABI_ACCESS_KEY', ''),
            'WASABI_SECRET_KEY': os.getenv('WASABI_SECRET_KEY', ''),
            'WASABI_BUCKET_NAME': os.getenv('WASABI_BUCKET_NAME', ''),
            'SMTP_SERVER': os.getenv('SMTP_SERVER', 'smtp.gmail.com'),
            'SMTP_PORT': os.getenv('SMTP_PORT', '587'),
            'SMTP_USERNAME': os.getenv('SMTP_USERNAME', ''),
            'SMTP_PASSWORD': os.getenv('SMTP_PASSWORD', ''),
            'REPORT_EMAIL': os.getenv('REPORT_EMAIL', 'it@go4rex.com'),
            'TELEGRAM_BOT_TOKEN': os.getenv('TELEGRAM_BOT_TOKEN', ''),
            'TELEGRAM_CHAT_ID': os.getenv('TELEGRAM_CHAT_ID', ''),
            'SLACK_WEBHOOK_URL': os.getenv('SLACK_WEBHOOK_URL', ''),
            'SCHEDULER_TIMEZONE': os.getenv('SCHEDULER_TIMEZONE', 'UTC'),
            'SCHEDULER_HOUR': os.getenv('SCHEDULER_HOUR', '0'),
            'SCHEDULER_MINUTE': os.getenv('SCHEDULER_MINUTE', '0'),
            'RECHECK_HOUR': os.getenv('RECHECK_HOUR', '2'),
            'CONTINUOUS_OFFLOAD_INTERVAL': os.getenv('CONTINUOUS_OFFLOAD_INTERVAL', '5'),
            'STORAGE_REPORT_INTERVAL': os.getenv('STORAGE_REPORT_INTERVAL', '60'),
            'TICKET_BACKUP_ENABLED': os.getenv('TICKET_BACKUP_ENABLED', 'true'),
            'TICKET_BACKUP_ENDPOINT': os.getenv('TICKET_BACKUP_ENDPOINT', 's3.eu-central-1.wasabisys.com'),
            'TICKET_BACKUP_BUCKET': os.getenv('TICKET_BACKUP_BUCKET', 'supportmailboxtickets'),
            'TICKET_BACKUP_INTERVAL_MINUTES': os.getenv('TICKET_BACKUP_INTERVAL_MINUTES', '1440'),
            'TICKET_BACKUP_DAILY_LIMIT': os.getenv('TICKET_BACKUP_DAILY_LIMIT', '0'),
        }
        
        # Merge with database settings (database takes priority)
        for key, value in env_settings.items():
            if key not in settings_dict:
                settings_dict[key] = value
        
        return render_template('settings.html', settings=settings_dict)
    finally:
        db.close()

@app.route('/tickets')
@login_required
def tickets():
    """List processed tickets"""
    import json
    # Ensure database schema is up to date
    try:
        from database import _migrate_database
        _migrate_database()
    except Exception:
        pass  # Migration already done or failed, continue anyway
    
    db = get_db()
    try:
        # Get settings from database for Wasabi client
        settings_dict = {}
        settings_list = db.query(Setting).all()
        for s in settings_list:
            settings_dict[s.key] = s.value
        
        # Get Wasabi configuration
        from config import reload_config, WASABI_ENDPOINT, WASABI_ACCESS_KEY, WASABI_SECRET_KEY, WASABI_BUCKET_NAME
        reload_config()
        
        endpoint = settings_dict.get('WASABI_ENDPOINT') or WASABI_ENDPOINT
        access_key = settings_dict.get('WASABI_ACCESS_KEY') or WASABI_ACCESS_KEY
        secret_key = settings_dict.get('WASABI_SECRET_KEY') or WASABI_SECRET_KEY
        bucket_name = settings_dict.get('WASABI_BUCKET_NAME') or WASABI_BUCKET_NAME
        
        # Initialize Wasabi client for URL generation
        wasabi_client = None
        if endpoint and access_key and secret_key and bucket_name:
            try:
                endpoint = endpoint.strip() if endpoint else ""
                if endpoint and not endpoint.startswith('http'):
                    endpoint = f"https://{endpoint}"
                wasabi_client = WasabiClient(
                    endpoint=endpoint,
                    access_key=access_key,
                    secret_key=secret_key,
                    bucket_name=bucket_name
                )
            except Exception as e:
                # If Wasabi client fails, we'll just not show URLs
                # Log the error but don't fail the page
                print(f"Warning: Could not initialize Wasabi client: {e}")
                pass
        
        page = request.args.get('page', 1, type=int)
        search_query = (request.args.get('q', '') or '').strip()
        status_filter = (request.args.get('status', '') or '').strip()
        sort_by = request.args.get('sort', 'processed_at')
        sort_order = request.args.get('order', 'desc')
        per_page = 50

        # Allowed sort columns
        sort_columns = {
            'ticket_id': ProcessedTicket.ticket_id,
            'processed_at': ProcessedTicket.processed_at,
            'attachments_count': ProcessedTicket.attachments_count,
            'status': ProcessedTicket.status,
        }
        sort_col = sort_columns.get(sort_by, ProcessedTicket.processed_at)
        order_fn = desc if sort_order == 'desc' else asc

        # Build query with optional search filter
        tickets_base_query = db.query(ProcessedTicket)
        if search_query:
            like_pattern = f"%{search_query}%"
            tickets_base_query = tickets_base_query.filter(
                or_(
                    cast(ProcessedTicket.ticket_id, String).like(like_pattern),
                    ProcessedTicket.status.like(like_pattern),
                    ProcessedTicket.error_message.like(like_pattern),
                    ProcessedTicket.wasabi_files.like(like_pattern)
                )
            )
        if status_filter:
            tickets_base_query = tickets_base_query.filter(
                ProcessedTicket.status == status_filter
            )

        # Manual pagination
        total = tickets_base_query.count()
        tickets_query = tickets_base_query.order_by(
            order_fn(sort_col)
        ).offset((page - 1) * per_page).limit(per_page).all()
        
        # Generate URLs for each ticket's files
        for ticket in tickets_query:
            ticket.wasabi_urls = []
            # Safely get wasabi_files attribute (may not exist in older database schemas)
            wasabi_files = getattr(ticket, 'wasabi_files', None)
            if wasabi_files and wasabi_client:
                try:
                    s3_keys = json.loads(wasabi_files)
                    for s3_key in s3_keys:
                        # Try presigned URL first, fallback to public URL
                        try:
                            url = wasabi_client.get_file_url(s3_key)
                            if not url:
                                url = wasabi_client.get_public_url(s3_key)
                            if url:
                                ticket.wasabi_urls.append({
                                    'url': url,
                                    'filename': s3_key.split('/')[-1] if '/' in s3_key else s3_key
                                })
                        except Exception:
                            # Skip this file if URL generation fails
                            pass
                except (json.JSONDecodeError, TypeError, AttributeError):
                    # Invalid JSON or missing attribute
                    pass
        
        # Create pagination object
        class Pagination:
            def __init__(self, page, per_page, total, items):
                self.page = page
                self.per_page = per_page
                self.total = total
                self.items = items
                self.pages = (total + per_page - 1) // per_page
                self.has_prev = page > 1
                self.has_next = page < self.pages
                self.prev_num = page - 1 if self.has_prev else None
                self.next_num = page + 1 if self.has_next else None
            
            def iter_pages(self, left_edge=2, right_edge=2, left_current=2, right_current=2):
                last = self.pages
                for num in range(1, last + 1):
                    if num <= left_edge or \
                       (num > self.page - left_current - 1 and num < self.page + right_current) or \
                       num > last - right_edge:
                        yield num
        
        tickets = Pagination(page, per_page, total, tickets_query)
        
        return render_template('tickets.html', tickets=tickets, q=search_query,
                               status_filter=status_filter, sort=sort_by, order=sort_order)
    finally:
        db.close()

@app.route('/storage')
@login_required
def storage_report():
    """Zendesk storage usage report — read from zendesk_storage_snapshot cache table"""
    db = get_db()
    try:
        page = request.args.get('page', 1, type=int)
        search_query = (request.args.get('q', '') or '').strip()
        sort_by = request.args.get('sort', 'size')
        sort_order = request.args.get('order', 'desc')
        status_filter = (request.args.get('status', '') or '').strip()
        per_page = 50

        from config import ZENDESK_SUBDOMAIN, reload_config, STORAGE_REPORT_INTERVAL
        reload_config()
        subdomain_row = db.query(Setting).filter_by(key='ZENDESK_SUBDOMAIN').first()
        subdomain = (subdomain_row.value if subdomain_row else None) or ZENDESK_SUBDOMAIN or 'app'

        sort_map = {
            'ticket_id':   ZendeskStorageSnapshot.ticket_id,
            'size':        ZendeskStorageSnapshot.total_size,
            'total_size':  ZendeskStorageSnapshot.total_size,
            'files':       ZendeskStorageSnapshot.attach_count,
            'updated':     ZendeskStorageSnapshot.updated_at,
            'last_seen_at': ZendeskStorageSnapshot.updated_at,
            'subject':     ZendeskStorageSnapshot.subject,
            'zd_status':   ZendeskStorageSnapshot.zd_status,
        }
        sort_col = sort_map.get(sort_by, ZendeskStorageSnapshot.total_size)
        order_fn = desc if sort_order == 'desc' else asc

        base_q = db.query(ZendeskStorageSnapshot).filter(
            ZendeskStorageSnapshot.total_size > 0
        )
        if search_query:
            lp = f"%{search_query}%"
            base_q = base_q.filter(
                or_(
                    cast(ZendeskStorageSnapshot.ticket_id, String).like(lp),
                    ZendeskStorageSnapshot.subject.like(lp),
                    ZendeskStorageSnapshot.zd_status.like(lp),
                )
            )
        if status_filter:
            base_q = base_q.filter(ZendeskStorageSnapshot.zd_status == status_filter)

        from sqlalchemy import func as sqlfunc
        totals = db.query(
            sqlfunc.count(ZendeskStorageSnapshot.id).label('count'),
            sqlfunc.sum(ZendeskStorageSnapshot.attach_count + ZendeskStorageSnapshot.inline_count).label('total_files'),
            sqlfunc.sum(ZendeskStorageSnapshot.total_size).label('total_bytes'),
        ).filter(ZendeskStorageSnapshot.total_size > 0).one()

        # Last update timestamp
        last_updated = db.query(sqlfunc.max(ZendeskStorageSnapshot.updated_at)).scalar()

        # Next scheduled run
        next_run = None
        try:
            sched = init_scheduler()
            if sched.scheduler.running:
                job = sched.scheduler.get_job('storage_snapshot')
                if job and job.next_run_time:
                    next_run = job.next_run_time.strftime('%d-%m-%Y %H:%M')
        except Exception:
            pass

        total_rows = base_q.count()
        rows = (
            base_q.order_by(order_fn(sort_col))
                  .offset((page - 1) * per_page)
                  .limit(per_page)
                  .all()
        )

        tickets_data = []
        for snap in rows:
            tickets_data.append({
                'ticket_id':  snap.ticket_id,
                'subject':    snap.subject or '',
                'zd_status':  snap.zd_status or '',
                'files':      (snap.attach_count or 0) + (snap.inline_count or 0),
                'attach':     snap.attach_count or 0,
                'inline':     snap.inline_count or 0,
                'size_bytes': snap.total_size or 0,
                'updated_at': snap.updated_at,
                'ticket_url': f'https://{subdomain}.zendesk.com/agent/tickets/{snap.ticket_id}',
            })

        # Status counts for filter tabs
        status_counts = {}
        for row in db.query(ZendeskStorageSnapshot.zd_status, sqlfunc.count(ZendeskStorageSnapshot.id))\
                     .filter(ZendeskStorageSnapshot.total_size > 0)\
                     .group_by(ZendeskStorageSnapshot.zd_status).all():
            status_counts[row[0] or ''] = row[1]

        class Pagination:
            def __init__(self, page, per_page, total):
                self.page = page; self.per_page = per_page; self.total = total
                self.pages = max(1, (total + per_page - 1) // per_page)
                self.has_prev = page > 1
                self.has_next = page < self.pages
                self.prev_num = page - 1 if self.has_prev else None
                self.next_num = page + 1 if self.has_next else None
            def iter_pages(self, left_edge=2, right_edge=2, left_current=2, right_current=2):
                for num in range(1, self.pages + 1):
                    if (num <= left_edge or
                            (num > self.page - left_current - 1 and num < self.page + right_current) or
                            num > self.pages - right_edge):
                        yield num

        is_empty = (db.query(ZendeskStorageSnapshot).count() == 0)

        # ── Scan progress ──────────────────────────
        snap_scanned = db.query(ZendeskStorageSnapshot).count()
        cache_total = db.query(ZendeskTicketCache).count()
        scan_pct = round(snap_scanned / cache_total * 100, 1) if cache_total else 0

        # ── Offloaded stats ─────────────────────────
        offloaded_tickets = db.query(ProcessedTicket).filter(
            ProcessedTicket.wasabi_files.isnot(None),
            ProcessedTicket.wasabi_files != '',
            ProcessedTicket.wasabi_files != '[]',
        ).count()
        tickets_with_files = db.query(ProcessedTicket).filter(
            ProcessedTicket.attachments_count > 0
        ).count()

        # Real offloaded bytes from Wasabi
        offloaded_bytes = 0
        try:
            from config import WASABI_ENDPOINT, WASABI_ACCESS_KEY, WASABI_SECRET_KEY, WASABI_BUCKET_NAME
            settings_dict = {s.key: s.value for s in db.query(Setting).all()}
            w_ep = (settings_dict.get('WASABI_ENDPOINT') or WASABI_ENDPOINT or '').strip()
            w_ak = settings_dict.get('WASABI_ACCESS_KEY') or WASABI_ACCESS_KEY
            w_sk = settings_dict.get('WASABI_SECRET_KEY') or WASABI_SECRET_KEY
            w_bk = settings_dict.get('WASABI_BUCKET_NAME') or WASABI_BUCKET_NAME
            if all([w_ep, w_ak, w_sk, w_bk]):
                if not w_ep.startswith('http'):
                    w_ep = f'https://{w_ep}'
                wc = WasabiClient(endpoint=w_ep, access_key=w_ak,
                                  secret_key=w_sk, bucket_name=w_bk)
                ws = wc.get_storage_stats()
                offloaded_bytes = ws.get('total_bytes', 0) or 0
        except Exception:
            pass

        # ── Plan limit from settings ────────────────
        limit_row = db.query(Setting).filter_by(key='ZENDESK_STORAGE_LIMIT_GB').first()
        plan_limit_gb = 0.0
        if limit_row and limit_row.value:
            try:
                plan_limit_gb = float(limit_row.value)
            except (ValueError, TypeError):
                pass

        return render_template(
            'storage.html',
            tickets=tickets_data,
            pagination=Pagination(page, per_page, total_rows),
            q=search_query,
            sort=sort_by,
            order=sort_order,
            status_filter=status_filter,
            status_counts=status_counts,
            total_tickets=totals.count or 0,
            total_files=totals.total_files or 0,
            total_bytes=totals.total_bytes or 0,
            last_updated=last_updated,
            next_run=next_run,
            is_empty=is_empty,
            storage_interval=STORAGE_REPORT_INTERVAL,
            scan_scanned=snap_scanned,
            scan_total=cache_total,
            scan_pct=scan_pct,
            offloaded_bytes=int(offloaded_bytes),
            offloaded_tickets=offloaded_tickets,
            tickets_with_files=tickets_with_files,
            plan_limit_gb=plan_limit_gb,
        )
    finally:
        db.close()


@app.route('/api/storage_report/refresh', methods=['POST'])
@login_required
def storage_report_refresh():
    """Manually trigger a storage snapshot refresh in the background"""
    try:
        sched = init_scheduler()
        import threading
        t = threading.Thread(target=sched.storage_snapshot_job, daemon=True, name='storage-snap-manual')
        t.start()
        return jsonify({'success': True, 'message': 'Storage snapshot refresh started in background.'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ── Closed-Ticket Backup ─────────────────────────────────────────────
@app.route('/ticket_backup')
@login_required
def ticket_backup():
    """Closed-ticket backup status page — shows ALL closed tickets (LEFT JOIN backup items)."""
    db = get_db()
    try:
        page = request.args.get('page', 1, type=int)
        search_query = (request.args.get('q', '') or '').strip()
        status_filter = (request.args.get('status', '') or '').strip()
        sort_by = request.args.get('sort', 'ticket_id')
        sort_order = request.args.get('order', 'desc')
        per_page = 50

        from sqlalchemy import func as sqlfunc, outerjoin, literal

        # ── aggregate counts from backup items ───────────────────────────
        status_counts = {}
        for row in db.query(TicketBackupItem.backup_status, sqlfunc.count(TicketBackupItem.id))\
                      .group_by(TicketBackupItem.backup_status).all():
            status_counts[row[0] or ''] = row[1]

        success_count = status_counts.get('success', 0)
        failed_count  = status_counts.get('failed', 0)
        pending_count = status_counts.get('pending', 0)
        skipped_count = status_counts.get('skipped', 0)

        total_bytes = db.query(sqlfunc.sum(TicketBackupItem.total_bytes)).filter(
            TicketBackupItem.backup_status == 'success'
        ).scalar() or 0
        total_files = db.query(sqlfunc.sum(TicketBackupItem.files_count)).filter(
            TicketBackupItem.backup_status == 'success'
        ).scalar() or 0

        closed_cache_count = db.query(sqlfunc.count(ZendeskTicketCache.ticket_id)).filter(
            ZendeskTicketCache.status == 'closed'
        ).scalar() or 0

        # ── LEFT JOIN: all closed cache tickets + their backup item (if any) ──
        # We build a combined list using a raw SQL approach for simplicity
        from sqlalchemy import text as sa_text

        sort_col_sql = {
            'ticket_id':      'c.ticket_id',
            'closed_at':      'b.closed_at',
            'backup_status':  "COALESCE(b.backup_status, 'pending')",
            'last_backup_at': 'b.last_backup_at',
            'files_count':    'COALESCE(b.files_count, 0)',
            'total_bytes':    'COALESCE(b.total_bytes, 0)',
        }.get(sort_by, 'c.ticket_id')
        order_dir = 'DESC' if sort_order == 'desc' else 'ASC'

        where_clauses = ["c.status = 'closed'"]
        params = {}

        if search_query:
            where_clauses.append(
                "(CAST(c.ticket_id AS TEXT) LIKE :sq "
                "OR COALESCE(b.backup_status,'pending') LIKE :sq "
                "OR COALESCE(b.s3_prefix,'') LIKE :sq "
                "OR COALESCE(b.last_error,'') LIKE :sq)"
            )
            params['sq'] = f'%{search_query}%'

        if status_filter:
            if status_filter == 'pending':
                where_clauses.append("b.ticket_id IS NULL")
            else:
                where_clauses.append("b.backup_status = :sf")
                params['sf'] = status_filter

        where_sql = ' AND '.join(where_clauses)

        count_sql = sa_text(
            f"SELECT COUNT(*) FROM zendesk_ticket_cache c "
            f"LEFT JOIN ticket_backup_items b ON c.ticket_id = b.ticket_id "
            f"WHERE {where_sql}"
        )
        total_rows = db.execute(count_sql, params).scalar() or 0

        data_sql = sa_text(
            f"SELECT c.ticket_id, c.subject, "
            f"  b.closed_at, b.last_backup_at, "
            f"  COALESCE(b.backup_status, 'pending') AS backup_status, "
            f"  COALESCE(b.files_count, 0) AS files_count, "
            f"  COALESCE(b.total_bytes, 0) AS total_bytes, "
            f"  COALESCE(b.s3_prefix, '') AS s3_prefix, "
            f"  COALESCE(b.last_error, '') AS last_error "
            f"FROM zendesk_ticket_cache c "
            f"LEFT JOIN ticket_backup_items b ON c.ticket_id = b.ticket_id "
            f"WHERE {where_sql} "
            f"ORDER BY {sort_col_sql} {order_dir} "
            f"LIMIT :lim OFFSET :off"
        )
        params['lim'] = per_page
        params['off'] = (page - 1) * per_page
        rows = db.execute(data_sql, params).fetchall()

        # pending = closed tickets with no backup item
        pending_count = closed_cache_count - success_count - failed_count - skipped_count
        status_counts['pending'] = max(0, pending_count)

        # Last run info
        last_run = db.query(TicketBackupRun).order_by(TicketBackupRun.run_date.desc()).first()

        items_data = []
        for row in rows:
            items_data.append({
                'ticket_id':     row.ticket_id,
                'subject':       row.subject or '',
                'closed_at':     row.closed_at,
                'last_backup_at': row.last_backup_at,
                'backup_status': row.backup_status,
                'files_count':   row.files_count or 0,
                'total_bytes':   row.total_bytes or 0,
                's3_prefix':     row.s3_prefix or '',
                'last_error':    row.last_error or '',
            })

        class Pagination:
            def __init__(self, page, per_page, total):
                self.page = page; self.per_page = per_page; self.total = total
                self.pages = max(1, (total + per_page - 1) // per_page)
                self.has_prev = page > 1
                self.has_next = page < self.pages
                self.prev_num = page - 1 if self.has_prev else None
                self.next_num = page + 1 if self.has_next else None
            def iter_pages(self, left_edge=2, right_edge=2, left_current=2, right_current=2):
                for num in range(1, self.pages + 1):
                    if (num <= left_edge or
                            (num > self.page - left_current - 1 and num < self.page + right_current) or
                            num > self.pages - right_edge):
                        yield num

        # Zendesk subdomain for ticket links
        from config import ZENDESK_SUBDOMAIN
        sub_row = db.query(Setting).filter_by(key='ZENDESK_SUBDOMAIN').first()
        subdomain = (sub_row.value if sub_row else None) or ZENDESK_SUBDOMAIN or 'app'

        return render_template(
            'ticket_backup.html',
            items=items_data,
            pagination=Pagination(page, per_page, total_rows),
            q=search_query,
            sort=sort_by,
            order=sort_order,
            status_filter=status_filter,
            status_counts=status_counts,
            total_items=total_rows,
            success_count=success_count,
            failed_count=failed_count,
            pending_count=pending_count,
            skipped_count=skipped_count,
            total_bytes=int(total_bytes),
            total_files=int(total_files),
            closed_cache_count=closed_cache_count,
            last_run=last_run,
            subdomain=subdomain,
        )
    finally:
        db.close()


@app.route('/api/ticket_backup_now', methods=['POST'])
@login_required
def ticket_backup_now():
    """Manually trigger a closed-ticket backup run."""
    try:
        sched = init_scheduler()
        sched.run_ticket_backup_now()
        return jsonify({'success': True, 'message': 'Closed-ticket backup started in background.'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/ticket_backup_backfill_html', methods=['POST'])
@login_required
def ticket_backup_backfill_html():
    """Backfill HTML exports for tickets that only have JSON in the bucket."""
    import threading
    from ticket_backup_manager import TicketBackupManager

    def _run():
        try:
            mgr = TicketBackupManager()
            result = mgr.backfill_html()
            logger.info(f"[BackfillHTML] Completed: {result}")
        except Exception as exc:
            logger.error(f"[BackfillHTML] Failed: {exc}", exc_info=True)

    t = threading.Thread(target=_run, daemon=True, name='backfill_html')
    t.start()
    return jsonify({'success': True, 'message': 'HTML backfill started in background — check logs for progress.'})


@app.route('/api/ticket_backup_status')
@login_required
def ticket_backup_status():
    """Return current ticket-backup job status."""
    try:
        sched = init_scheduler()
        status = sched.get_ticket_backup_status()
        return jsonify({'success': True, **status})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/storage_report')
@login_required
def storage_report_json():
    """JSON version of the storage report for the React Explorer frontend"""
    db = get_db()
    try:
        from sqlalchemy import func as sqlfunc
        from config import ZENDESK_SUBDOMAIN

        rows = db.query(ZendeskStorageSnapshot).filter(
            ZendeskStorageSnapshot.total_size > 0
        ).order_by(ZendeskStorageSnapshot.total_size.desc()).all()

        totals = db.query(
            sqlfunc.count(ZendeskStorageSnapshot.id).label('count'),
            sqlfunc.sum(ZendeskStorageSnapshot.attach_count + ZendeskStorageSnapshot.inline_count).label('total_files'),
            sqlfunc.sum(ZendeskStorageSnapshot.total_size).label('total_bytes'),
        ).filter(ZendeskStorageSnapshot.total_size > 0).one()

        by_status = {}
        for row in db.query(
            ZendeskStorageSnapshot.zd_status,
            sqlfunc.count(ZendeskStorageSnapshot.id),
            sqlfunc.sum(ZendeskStorageSnapshot.attach_count + ZendeskStorageSnapshot.inline_count),
            sqlfunc.sum(ZendeskStorageSnapshot.total_size),
        ).filter(ZendeskStorageSnapshot.total_size > 0).group_by(ZendeskStorageSnapshot.zd_status).all():
            by_status[row[0] or ''] = {'tickets': row[1], 'files': row[2] or 0, 'bytes': row[3] or 0}

        last_updated = db.query(sqlfunc.max(ZendeskStorageSnapshot.updated_at)).scalar()
        next_run = None
        try:
            sched = init_scheduler()
            if sched.scheduler.running:
                job = sched.scheduler.get_job('storage_snapshot')
                if job and job.next_run_time:
                    next_run = job.next_run_time.isoformat()
        except Exception:
            pass

        # Scan progress: how many tickets scanned vs total in cache
        snap_scanned = db.query(ZendeskStorageSnapshot).count()
        cache_total = db.query(ZendeskTicketCache).count()
        is_empty = (snap_scanned == 0)

        # Offloaded stats from ProcessedTicket
        offloaded_tickets = db.query(ProcessedTicket).filter(
            ProcessedTicket.wasabi_files.isnot(None),
            ProcessedTicket.wasabi_files != '',
            ProcessedTicket.wasabi_files != '[]',
        ).count()
        tickets_with_files = db.query(ProcessedTicket).filter(
            ProcessedTicket.attachments_count > 0
        ).count()

        # Plan limit from settings
        limit_row = db.query(Setting).filter_by(key='ZENDESK_STORAGE_LIMIT_GB').first()
        plan_limit_gb = 0.0
        if limit_row and limit_row.value:
            try:
                plan_limit_gb = float(limit_row.value)
            except (ValueError, TypeError):
                pass

        # Wasabi total bytes = real offloaded amount
        offloaded_bytes = 0
        try:
            from config import (WASABI_ENDPOINT, WASABI_ACCESS_KEY,
                                WASABI_SECRET_KEY, WASABI_BUCKET_NAME)
            settings_dict = {s.key: s.value for s in db.query(Setting).all()}
            w_ep = (settings_dict.get('WASABI_ENDPOINT') or WASABI_ENDPOINT or '').strip()
            w_ak = settings_dict.get('WASABI_ACCESS_KEY') or WASABI_ACCESS_KEY
            w_sk = settings_dict.get('WASABI_SECRET_KEY') or WASABI_SECRET_KEY
            w_bk = settings_dict.get('WASABI_BUCKET_NAME') or WASABI_BUCKET_NAME
            if all([w_ep, w_ak, w_sk, w_bk]):
                if not w_ep.startswith('http'):
                    w_ep = f'https://{w_ep}'
                wc = WasabiClient(endpoint=w_ep, access_key=w_ak,
                                  secret_key=w_sk, bucket_name=w_bk)
                ws = wc.get_storage_stats()
                offloaded_bytes = ws.get('total_bytes', 0) or 0
        except Exception:
            pass

        return jsonify({
            'rows': [{
                'ticket_id': s.ticket_id,
                'subject': s.subject or '',
                'zd_status': s.zd_status or '',
                'attach_count': s.attach_count or 0,
                'inline_count': s.inline_count or 0,
                'total_size': s.total_size or 0,
                'last_seen_at': s.last_seen_at.isoformat() if s.last_seen_at else None,
            } for s in rows],
            'summary': {
                'total_tickets': totals.count or 0,
                'total_files': int(totals.total_files or 0),
                'total_bytes': int(totals.total_bytes or 0),
                'by_status': by_status,
            },
            'scan': {
                'scanned': snap_scanned,
                'total': cache_total,
                'pct': round(snap_scanned / cache_total * 100, 1) if cache_total else 0,
            },
            'offloaded': {
                'bytes': int(offloaded_bytes),
                'tickets': offloaded_tickets,
                'tickets_with_files': tickets_with_files,
            },
            'plan_limit_gb': plan_limit_gb,
            'last_updated': last_updated.isoformat() if last_updated else None,
            'next_run': next_run,
            'is_empty': is_empty,
        })
    finally:
        db.close()


@app.route('/api/ticket_sizes')
@login_required
def ticket_sizes_json():
    """Return {ticket_id: total_size_bytes} for a list of ticket IDs.
    Query param: ids=1,2,3 (comma-separated). Used by Explorer Tickets panel."""
    ids_raw = request.args.get('ids', '').strip()
    if not ids_raw:
        return jsonify({})
    try:
        ticket_ids = [int(x) for x in ids_raw.split(',') if x.strip().isdigit()]
    except ValueError:
        return jsonify({})
    if not ticket_ids:
        return jsonify({})
    db = get_db()
    try:
        rows = db.query(
            ZendeskStorageSnapshot.ticket_id,
            ZendeskStorageSnapshot.total_size,
        ).filter(ZendeskStorageSnapshot.ticket_id.in_(ticket_ids)).all()
        return jsonify({str(r.ticket_id): r.total_size or 0 for r in rows})
    finally:
        db.close()


@app.route('/api/ticket_status')
@login_required
def ticket_status_json():
    """Return offload + backup status for a batch of ticket IDs.
    Query param: ids=1,2,3 (comma-separated).
    Response: { "<id>": { offloaded: bool, attachments_count: int, inlines_count: int,
                           processed_at: str|null, backup_status: str|null, backed_up_at: str|null } }
    """
    ids_raw = request.args.get('ids', '').strip()
    if not ids_raw:
        return jsonify({})
    try:
        ticket_ids = [int(x) for x in ids_raw.split(',') if x.strip().isdigit()]
    except ValueError:
        return jsonify({})
    if not ticket_ids:
        return jsonify({})
    db = get_db()
    try:
        result: dict = {str(tid): {
            'offloaded': False,
            'attachments_count': 0,
            'inlines_count': 0,
            'processed_at': None,
            'backup_status': None,
            'backed_up_at': None,
        } for tid in ticket_ids}

        # Offload status from processed_tickets
        offload_rows = db.query(
            ProcessedTicket.ticket_id,
            ProcessedTicket.status,
            ProcessedTicket.attachments_count,
            ProcessedTicket.processed_at,
            ProcessedTicket.wasabi_files,
        ).filter(ProcessedTicket.ticket_id.in_(ticket_ids)).all()
        for row in offload_rows:
            key = str(row.ticket_id)
            if key in result:
                result[key]['offloaded'] = row.status == 'processed'
                result[key]['attachments_count'] = row.attachments_count or 0
                result[key]['processed_at'] = row.processed_at.isoformat() if row.processed_at else None
                # count inlines from wasabi_files JSON
                try:
                    import json as _json
                    files = _json.loads(row.wasabi_files or '[]')
                    result[key]['inlines_count'] = sum(
                        1 for f in files if isinstance(f, str) and '/inlines/' in f
                    )
                except Exception:
                    pass

        # Backup status from ticket_backup_items
        backup_rows = db.query(
            TicketBackupItem.ticket_id,
            TicketBackupItem.backup_status,
            TicketBackupItem.last_backup_at,
        ).filter(TicketBackupItem.ticket_id.in_(ticket_ids)).all()
        for row in backup_rows:
            key = str(row.ticket_id)
            if key in result:
                result[key]['backup_status'] = row.backup_status
                result[key]['backed_up_at'] = row.last_backup_at.isoformat() if row.last_backup_at else None

        return jsonify(result)
    finally:
        db.close()


@app.route('/api/wasabi_stats')
@login_required
def wasabi_stats_json():
    """Return Wasabi storage stats as JSON for the Explorer frontend"""
    try:
        from config import (reload_config, WASABI_ENDPOINT, WASABI_ACCESS_KEY,
                            WASABI_SECRET_KEY, WASABI_BUCKET_NAME)
        reload_config()
        db = get_db()
        try:
            settings_dict = {s.key: s.value for s in db.query(Setting).all()}
        finally:
            db.close()
        endpoint = (settings_dict.get('WASABI_ENDPOINT') or WASABI_ENDPOINT or '').strip()
        access_key = settings_dict.get('WASABI_ACCESS_KEY') or WASABI_ACCESS_KEY
        secret_key = settings_dict.get('WASABI_SECRET_KEY') or WASABI_SECRET_KEY
        bucket_name = settings_dict.get('WASABI_BUCKET_NAME') or WASABI_BUCKET_NAME
        if not all([endpoint, access_key, secret_key, bucket_name]):
            return jsonify({'error': 'Wasabi not configured'}), 503
        if not endpoint.startswith('http'):
            endpoint = f'https://{endpoint}'
        wasabi = WasabiClient(endpoint=endpoint, access_key=access_key,
                              secret_key=secret_key, bucket_name=bucket_name)
        stats = wasabi.get_storage_stats()
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/explorer/settings')
@login_required
def explorer_settings_api():
    """Return Zendesk credentials from the z2w database for the Explorer app.
    The Explorer React app calls this on mount so users don't have to
    re-enter credentials that are already saved in z2w Settings."""
    db = get_db()
    try:
        from config import reload_config, ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, ZENDESK_API_TOKEN
        reload_config()
        settings_dict = {}
        for s in db.query(Setting).all():
            settings_dict[s.key] = s.value or ''

        subdomain = (settings_dict.get('ZENDESK_SUBDOMAIN') or ZENDESK_SUBDOMAIN or '').strip()
        email     = (settings_dict.get('ZENDESK_EMAIL')     or ZENDESK_EMAIL     or '').strip()
        token     = (settings_dict.get('ZENDESK_API_TOKEN') or ZENDESK_API_TOKEN or '').strip()

        # Clean subdomain: strip URL parts if someone pasted the full URL
        subdomain = subdomain.replace('https://', '').replace('http://', '').replace('.zendesk.com', '').split('.')[0]

        return jsonify({
            'subdomain': subdomain,
            'email': email,
            'token': token,
            'configured': bool(subdomain and token),
        })
    finally:
        db.close()


@app.route('/explorer/')
@login_required
def explorer_app(subpath=''):
    """Render the Zendesk Explorer inside the unified Flask shell"""
    import os as _os
    static_dir = _os.path.join(_os.path.dirname(__file__), 'static', 'explorer', 'app')
    built = _os.path.isfile(_os.path.join(static_dir, 'index.html'))
    return render_template('explorer.html', explorer_built=built)


@app.route('/explorer/app/')
@app.route('/explorer/app/<path:subpath>')
@login_required
def explorer_static(subpath=''):
    """Serve the Next.js static files (assets + HTML) for the embedded explorer"""
    import os as _os
    from flask import send_from_directory, abort
    static_dir = _os.path.join(_os.path.dirname(__file__), 'static', 'explorer', 'app')
    if not _os.path.isdir(static_dir):
        return abort(404)
    candidate = _os.path.join(static_dir, subpath) if subpath else None
    if candidate and _os.path.isfile(candidate):
        return send_from_directory(static_dir, subpath)
    root_index = _os.path.join(static_dir, 'index.html')
    if _os.path.isfile(root_index):
        return send_from_directory(static_dir, 'index.html')
    return abort(404)


@app.route('/explorer/api/proxy')
@login_required
def explorer_zendesk_proxy():
    """Server-side proxy for Zendesk API calls from the React Explorer.
    Needed because Next.js API routes are dropped in static export mode."""
    from flask import Response
    subdomain = request.args.get('subdomain', '').strip()
    path = request.args.get('path', '').strip()
    if not subdomain or not path:
        return jsonify({'error': 'Missing subdomain or path'}), 400
    auth = request.headers.get('Authorization', '')
    if not auth:
        return jsonify({'error': 'Missing Authorization header'}), 401
    target = f"https://{subdomain}.zendesk.com/api/v2{path}"
    try:
        resp = requests.get(
            target,
            headers={'Authorization': auth, 'Content-Type': 'application/json'},
            timeout=30,
        )
        return Response(
            resp.content,
            status=resp.status_code,
            content_type=resp.headers.get('Content-Type', 'application/json'),
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/logs')
@login_required
def logs():
    """View offload logs"""
    import json
    db = get_db()
    try:
        # Get settings from database for Wasabi client
        settings_dict = {}
        settings_list = db.query(Setting).all()
        for s in settings_list:
            settings_dict[s.key] = s.value
        
        # Get Wasabi configuration
        from config import reload_config, WASABI_ENDPOINT, WASABI_ACCESS_KEY, WASABI_SECRET_KEY, WASABI_BUCKET_NAME
        reload_config()
        
        endpoint = settings_dict.get('WASABI_ENDPOINT') or WASABI_ENDPOINT
        access_key = settings_dict.get('WASABI_ACCESS_KEY') or WASABI_ACCESS_KEY
        secret_key = settings_dict.get('WASABI_SECRET_KEY') or WASABI_SECRET_KEY
        bucket_name = settings_dict.get('WASABI_BUCKET_NAME') or WASABI_BUCKET_NAME
        
        # Initialize Wasabi client for URL generation
        wasabi_client = None
        if endpoint and access_key and secret_key and bucket_name:
            try:
                endpoint = endpoint.strip() if endpoint else ""
                if endpoint and not endpoint.startswith('http'):
                    endpoint = f"https://{endpoint}"
                wasabi_client = WasabiClient(
                    endpoint=endpoint,
                    access_key=access_key,
                    secret_key=secret_key,
                    bucket_name=bucket_name
                )
            except Exception as e:
                print(f"Warning: Could not initialize Wasabi client: {e}")
                pass
        
        page = request.args.get('page', 1, type=int)
        search_query = (request.args.get('q', '') or '').strip()
        status_filter = (request.args.get('status', '') or '').strip()
        sort_by = request.args.get('sort', 'run_date')
        sort_order = request.args.get('order', 'desc')
        per_page = 20

        # Allowed sort columns
        sort_columns = {
            'run_date': OffloadLog.run_date,
            'tickets_processed': OffloadLog.tickets_processed,
            'attachments_uploaded': OffloadLog.attachments_uploaded,
            'errors_count': OffloadLog.errors_count,
            'status': OffloadLog.status,
        }
        sort_col = sort_columns.get(sort_by, OffloadLog.run_date)
        order_fn = desc if sort_order == 'desc' else asc

        # Build query with optional filters
        base_query = db.query(OffloadLog)
        if search_query:
            like_pattern = f"%{search_query}%"
            base_query = base_query.filter(
                or_(
                    cast(OffloadLog.tickets_processed, String).like(like_pattern),
                    OffloadLog.status.like(like_pattern),
                    OffloadLog.details.like(like_pattern),
                )
            )
        if status_filter:
            base_query = base_query.filter(OffloadLog.status == status_filter)

        # Manual pagination
        total = base_query.count()
        logs_query = base_query.order_by(
            order_fn(sort_col)
        ).offset((page - 1) * per_page).limit(per_page).all()
        
        # Generate URLs for each log's files
        for log in logs_query:
            log.wasabi_urls = []
            if log.details and wasabi_client:
                try:
                    # Try to parse as JSON (new format)
                    log_data = json.loads(log.details)
                    if isinstance(log_data, dict) and "all_s3_keys" in log_data:
                        for file_info in log_data["all_s3_keys"]:
                            s3_key = file_info.get("s3_key")
                            if s3_key:
                                try:
                                    url = wasabi_client.get_file_url(s3_key)
                                    if not url:
                                        url = wasabi_client.get_public_url(s3_key)
                                    if url:
                                        log.wasabi_urls.append({
                                            'url': url,
                                            'filename': file_info.get("original_filename", s3_key.split('/')[-1] if '/' in s3_key else s3_key),
                                            'ticket_id': file_info.get("ticket_id")
                                        })
                                except Exception:
                                    pass
                    # Fallback: try to extract from old string format
                    elif isinstance(log_data, str):
                        # Old format - try to extract S3 keys from string representation
                        # This is a fallback for older logs
                        pass
                except (json.JSONDecodeError, TypeError, AttributeError):
                    # Invalid JSON or old format - try to parse as string
                    try:
                        # For old logs stored as string, we can't easily extract S3 keys
                        # They would need to be reprocessed
                        pass
                    except Exception:
                        pass
        
        # Create pagination object
        class Pagination:
            def __init__(self, page, per_page, total, items):
                self.page = page
                self.per_page = per_page
                self.total = total
                self.items = items
                self.pages = (total + per_page - 1) // per_page
                self.has_prev = page > 1
                self.has_next = page < self.pages
                self.prev_num = page - 1 if self.has_prev else None
                self.next_num = page + 1 if self.has_next else None
            
            def iter_pages(self, left_edge=2, right_edge=2, left_current=2, right_current=2):
                last = self.pages
                for num in range(1, last + 1):
                    if num <= left_edge or \
                       (num > self.page - left_current - 1 and num < self.page + right_current) or \
                       num > last - right_edge:
                        yield num
        
        logs = Pagination(page, per_page, total, logs_query)

        # Collect distinct statuses for filter dropdown
        all_statuses = [r[0] for r in db.query(OffloadLog.status).distinct().all() if r[0]]

        return render_template('logs.html', logs=logs, q=search_query,
                               status_filter=status_filter, sort=sort_by, order=sort_order,
                               all_statuses=sorted(all_statuses))
    finally:
        db.close()

@app.route('/privacy')
def privacy():
    """Privacy Policy page"""
    return render_template('privacy.html')

@app.route('/cookies')
def cookies():
    """Cookie Policy page"""
    return render_template('cookies.html')

@app.route('/debug/login-info')
def debug_login_info():
    """Debug endpoint to check expected login credentials (temporary - remove in production)"""
    db = get_db()
    try:
        admin_username_setting = db.query(Setting).filter_by(key='ADMIN_USERNAME').first()
        admin_password_setting = db.query(Setting).filter_by(key='ADMIN_PASSWORD').first()
        
        expected_username = admin_username_setting.value if admin_username_setting else ADMIN_USERNAME
        expected_password = admin_password_setting.value if admin_password_setting else ADMIN_PASSWORD
        
        return jsonify({
            'username': expected_username,
            'username_length': len(expected_username) if expected_username else 0,
            'password_length': len(expected_password) if expected_password else 0,
            'password_first_5': expected_password[:5] if expected_password else '',
            'password_last_5': expected_password[-5:] if expected_password else '',
            'source_username': 'database' if admin_username_setting else 'config',
            'source_password': 'database' if admin_password_setting else 'config',
        })
    finally:
        db.close()

@app.route('/api/test_connection/<connection_type>', methods=['POST'])
@login_required
def test_connection(connection_type):
    """Test Zendesk and Wasabi connections"""
    
    if connection_type == 'zendesk':
        try:
            # Get settings from database first, then fall back to .env
            db = get_db()
            try:
                settings_dict = {}
                settings_list = db.query(Setting).all()
                for s in settings_list:
                    settings_dict[s.key] = s.value
                
                # Use database settings if available, otherwise use .env
                from config import reload_config, ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, ZENDESK_API_TOKEN
                reload_config()
                
                # Update environment variables with database values if they exist
                import os
                if settings_dict.get('ZENDESK_SUBDOMAIN'):
                    os.environ['ZENDESK_SUBDOMAIN'] = settings_dict['ZENDESK_SUBDOMAIN']
                if settings_dict.get('ZENDESK_EMAIL'):
                    os.environ['ZENDESK_EMAIL'] = settings_dict['ZENDESK_EMAIL']
                if settings_dict.get('ZENDESK_API_TOKEN'):
                    os.environ['ZENDESK_API_TOKEN'] = settings_dict['ZENDESK_API_TOKEN']
                
                # Reload config again to pick up the updated env vars
                reload_config()
            finally:
                db.close()
            
            client = ZendeskClient()
            tickets = client.get_all_tickets()
            return jsonify({'success': True, 'message': f'Connected! Found {len(tickets)} tickets'})
        except ValueError as e:
            return jsonify({'success': False, 'message': str(e)})
        except Exception as e:
            return jsonify({'success': False, 'message': f'Connection error: {str(e)}'})
    
    elif connection_type == 'wasabi':
        try:
            # Get settings from database first, then fall back to .env
            db = get_db()
            try:
                settings_dict = {}
                settings_list = db.query(Setting).all()
                for s in settings_list:
                    settings_dict[s.key] = s.value
                
                # Use database settings if available, otherwise use .env
                from config import reload_config, WASABI_ENDPOINT, WASABI_ACCESS_KEY, WASABI_SECRET_KEY, WASABI_BUCKET_NAME
                reload_config()
                
                endpoint = settings_dict.get('WASABI_ENDPOINT') or WASABI_ENDPOINT
                access_key = settings_dict.get('WASABI_ACCESS_KEY') or WASABI_ACCESS_KEY
                secret_key = settings_dict.get('WASABI_SECRET_KEY') or WASABI_SECRET_KEY
                bucket_name = settings_dict.get('WASABI_BUCKET_NAME') or WASABI_BUCKET_NAME
            finally:
                db.close()
            
            # Validate endpoint format
            endpoint = endpoint.strip() if endpoint else ""
            if endpoint and not endpoint.startswith('http'):
                endpoint = f"https://{endpoint}"
            
            client = WasabiClient(
                endpoint=endpoint,
                access_key=access_key,
                secret_key=secret_key,
                bucket_name=bucket_name
            )
            success, message = client.test_connection()
            return jsonify({'success': success, 'message': message})
        except Exception as e:
            return jsonify({'success': False, 'message': f'Connection error: {str(e)}'})
    
    elif connection_type == 'telegram':
        try:
            from config import reload_config
            reload_config()
            db = get_db()
            try:
                settings_dict = {}
                for s in db.query(Setting).all():
                    settings_dict[s.key] = s.value
                import os
                if settings_dict.get('TELEGRAM_BOT_TOKEN'):
                    os.environ['TELEGRAM_BOT_TOKEN'] = settings_dict['TELEGRAM_BOT_TOKEN']
                if settings_dict.get('TELEGRAM_CHAT_ID'):
                    os.environ['TELEGRAM_CHAT_ID'] = settings_dict['TELEGRAM_CHAT_ID']
                reload_config()
            finally:
                db.close()
            from telegram_reporter import TelegramReporter
            reporter = TelegramReporter()
            if not reporter.bot_token or not reporter.chat_id:
                return jsonify({'success': False, 'message': 'Bot token or chat ID not configured'})
            sent = reporter.send_message('✅ <b>Test message from z2w</b>\nConnection successful!')
            if sent:
                return jsonify({'success': True, 'message': 'Test message sent to Telegram!'})
            else:
                return jsonify({'success': False, 'message': 'Failed to send message — check token and chat ID'})
        except Exception as e:
            return jsonify({'success': False, 'message': f'Telegram error: {str(e)}'})

    elif connection_type == 'slack':
        try:
            from config import reload_config
            reload_config()
            db = get_db()
            try:
                settings_dict = {}
                for s in db.query(Setting).all():
                    settings_dict[s.key] = s.value
                import os
                if settings_dict.get('SLACK_WEBHOOK_URL'):
                    os.environ['SLACK_WEBHOOK_URL'] = settings_dict['SLACK_WEBHOOK_URL']
                reload_config()
            finally:
                db.close()
            from slack_reporter import SlackReporter
            reporter = SlackReporter()
            if not reporter.webhook_url:
                return jsonify({'success': False, 'message': 'Webhook URL not configured'})
            import requests as req
            resp = req.post(reporter.webhook_url, json={'text': '✅ Test message from z2w — connection successful!'}, timeout=10)
            if resp.status_code == 200:
                return jsonify({'success': True, 'message': 'Test message sent to Slack!'})
            else:
                return jsonify({'success': False, 'message': f'Slack returned status {resp.status_code}: {resp.text[:200]}'})
        except Exception as e:
            return jsonify({'success': False, 'message': f'Slack error: {str(e)}'})

    return jsonify({'success': False, 'message': f'Unknown connection type: {connection_type}'})

@app.route('/api/run_now', methods=['POST'])
@login_required
def run_now():
    """Manually trigger offload"""
    try:
        sched = init_scheduler()
        sched.run_now()
        return jsonify({'success': True, 'message': 'Offload job started'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/backup_now', methods=['POST'])
@login_required
def backup_now():
    """Manually trigger backup"""
    try:
        sched = init_scheduler()
        sched.run_backup_now()
        return jsonify({'success': True, 'message': 'Backup job started'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/recheck_all', methods=['POST'])
@login_required
def recheck_all():
    """Scan all Zendesk tickets and process any that still have attachments"""
    try:
        sched = init_scheduler()
        import threading
        t = threading.Thread(target=sched.run_recheck_all_now, daemon=True)
        t.start()
        return jsonify({'success': True, 'message': 'Recheck-all job started in background.'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/sync_ticket_cache', methods=['POST'])
@login_required
def sync_ticket_cache():
    """Manually trigger a full Zendesk ticket cache sync in the background."""
    try:
        sched = init_scheduler()
        import threading
        def _run():
            try:
                sched.offloader.sync_ticket_cache()
            except Exception as e:
                logger.error(f"Manual cache sync failed: {e}", exc_info=True)
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return jsonify({'success': True, 'message': 'Ticket cache sync started in background.'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/ticket_cache_stats', methods=['GET'])
@login_required
def ticket_cache_stats():
    """Return current ticket cache statistics."""
    db = get_db()
    try:
        total = db.query(ZendeskTicketCache).count()
        from sqlalchemy import func as sqlfunc
        last_sync = db.query(sqlfunc.max(ZendeskTicketCache.cached_at)).scalar()
        return jsonify({
            'total': total,
            'last_sync': last_sync.isoformat() if last_sync else None,
        })
    except Exception as e:
        return jsonify({'error': str(e)})
    finally:
        db.close()

@app.route('/api/skipped_tickets', methods=['GET'])
@login_required
def skipped_tickets():
    """
    Return tickets that were recorded with 0 attachments uploaded AND have an error_message,
    meaning they were attempted but failed / skipped. These are candidates for manual re-offload.
    Query params:
        page  (int, default 1)
        limit (int, default 200)
    """
    db = get_db()
    try:
        page  = max(1, int(request.args.get('page',  1)))
        limit = min(500, max(1, int(request.args.get('limit', 200))))
        offset = (page - 1) * limit

        base_q = db.query(ProcessedTicket).filter(
            ProcessedTicket.attachments_count == 0,
            ProcessedTicket.error_message != None,
        )
        total = base_q.count()
        rows  = base_q.order_by(ProcessedTicket.ticket_id.asc()).offset(offset).limit(limit).all()

        tickets = [
            {
                'ticket_id':    r.ticket_id,
                'processed_at': r.processed_at.isoformat() if r.processed_at else None,
                'error_message': r.error_message,
                'status':        r.status,
            }
            for r in rows
        ]
        return jsonify({'tickets': tickets, 'total': total, 'page': page, 'limit': limit})
    except Exception as e:
        return jsonify({'error': str(e)})
    finally:
        db.close()

@app.route('/api/recheck_status', methods=['GET'])
@login_required
def recheck_status():
    """Return live recheck-all status + last summary for polling"""
    try:
        sched = init_scheduler()
        status = sched.get_recheck_status()
        summary = status.get('summary')
        # Recursively convert datetime objects to strings for JSON serialisation
        if summary:
            summary = _sanitize_for_json(summary)
        return jsonify({
            'running': status['running'],
            'started_at': status['started_at'],
            'progress': status.get('progress', {}),
            'summary': summary,
            'next_scheduled_run': status.get('next_scheduled_run'),
        })
    except Exception as e:
        return jsonify({'running': False, 'started_at': None, 'progress': {}, 'summary': None, 'error': str(e)})

@app.route('/api/offload_ticket/<int:ticket_id>', methods=['POST'])
@login_required
def offload_ticket(ticket_id):
    """Re-process a single ticket — upload any remaining attachments to Wasabi."""
    try:
        sched = init_scheduler()
        # Block if a global job is running to avoid conflicts
        if sched._job_running or getattr(sched, '_recheck_running', False):
            return jsonify({'success': False, 'message': 'A job is already running — please wait.'})

        result = sched.offloader.process_ticket(ticket_id)
        uploaded = result.get('attachments_uploaded', 0)
        errors = result.get('errors', [])
        status_msg = f"{uploaded} file(s) uploaded"
        if errors:
            status_msg += f", {len(errors)} error(s)"
        return jsonify({
            'success': True,
            'ticket_id': ticket_id,
            'attachments_uploaded': uploaded,
            'errors': errors,
            'message': status_msg,
        })
    except Exception as e:
        logger.error(f"Error offloading ticket {ticket_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)})

@app.route('/recheck_report')
@login_required
def recheck_report():
    """Dedicated page showing live progress and the last completed recheck-all report"""
    import json
    sched = init_scheduler()
    status = sched.get_recheck_status()
    running = status['running']
    summary = status.get('summary')

    # Get Zendesk subdomain for ticket links
    from config import reload_config, ZENDESK_SUBDOMAIN
    reload_config()
    db = get_db()
    try:
        sd_setting = db.query(Setting).filter_by(key='ZENDESK_SUBDOMAIN').first()
        zendesk_subdomain = (sd_setting.value if sd_setting else None) or ZENDESK_SUBDOMAIN or ''

        # Also try to load the most recent recheck_all OffloadLog from DB as fallback
        logs = db.query(OffloadLog).order_by(OffloadLog.run_date.desc()).all()
        last_log = None
        for log in logs:
            if log.details:
                try:
                    d = json.loads(log.details)
                    if isinstance(d, dict) and d.get('run_mode') == 'recheck_all':
                        last_log = log
                        last_log._parsed = d
                        break
                except Exception:
                    pass
    finally:
        db.close()

    # Sanitize summary for JSON serialization (datetime objects → ISO strings)
    safe_summary = None
    if summary:
        safe_summary = _sanitize_for_json(summary)

    return render_template(
        'recheck_report.html',
        running=running,
        started_at=status.get('started_at'),
        progress=status.get('progress', {}),
        summary=safe_summary,
        last_log=last_log,
        zendesk_subdomain=zendesk_subdomain,
    )

@app.route('/api/scheduler/start', methods=['POST'])
@login_required
def start_scheduler():
    """Start scheduler"""
    try:
        sched = init_scheduler()
        if not sched.scheduler.running:
            sched.start()
        return jsonify({'success': True, 'message': 'Scheduler started'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/scheduler/stop', methods=['POST'])
@login_required
def stop_scheduler():
    """Stop scheduler"""
    try:
        sched = init_scheduler()
        if sched.scheduler.running:
            sched.stop()
        return jsonify({'success': True, 'message': 'Scheduler stopped'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/scheduler/status', methods=['GET'])
@login_required
def scheduler_status():
    """Get scheduler status"""
    sched = init_scheduler()
    jobs = sched.scheduler.get_jobs()
    next_run = jobs[0].next_run_time if jobs else None
    
    return jsonify({
        'running': sched.scheduler.running,
        'next_run': next_run.isoformat() if next_run else None
    })

@app.route('/api/scheduler/update', methods=['POST'])
@login_required
def update_scheduler_settings():
    """Update scheduler settings and restart scheduler"""
    try:
        data = request.json
        timezone = data.get('SCHEDULER_TIMEZONE', 'UTC')
        hour = int(data.get('SCHEDULER_HOUR', 0))
        minute = int(data.get('SCHEDULER_MINUTE', 0))
        
        # Validate inputs
        if hour < 0 or hour > 23:
            return jsonify({'success': False, 'message': 'Hour must be between 0 and 23'}), 400
        if minute < 0 or minute > 59:
            return jsonify({'success': False, 'message': 'Minute must be between 0 and 59'}), 400
        
        # Update settings in database
        db = get_db()
        try:
            for key, value in [('SCHEDULER_TIMEZONE', timezone), ('SCHEDULER_HOUR', str(hour)), ('SCHEDULER_MINUTE', str(minute))]:
                setting = db.query(Setting).filter_by(key=key).first()
                if setting:
                    setting.value = value
                    setting.updated_at = datetime.utcnow()
                else:
                    setting = Setting(key=key, value=value)
                    db.add(setting)
            db.commit()
        finally:
            db.close()
        
        # Update .env file
        from config import BASE_DIR
        env_file = BASE_DIR / '.env'
        env_lines = []
        if env_file.exists():
            with open(env_file, 'r') as f:
                env_lines = f.readlines()
        
        new_lines = []
        updated = {'SCHEDULER_TIMEZONE': False, 'SCHEDULER_HOUR': False, 'SCHEDULER_MINUTE': False}
        for line in env_lines:
            if line.strip().startswith('SCHEDULER_TIMEZONE='):
                new_lines.append(f'SCHEDULER_TIMEZONE={timezone}\n')
                updated['SCHEDULER_TIMEZONE'] = True
            elif line.strip().startswith('SCHEDULER_HOUR='):
                new_lines.append(f'SCHEDULER_HOUR={hour}\n')
                updated['SCHEDULER_HOUR'] = True
            elif line.strip().startswith('SCHEDULER_MINUTE='):
                new_lines.append(f'SCHEDULER_MINUTE={minute}\n')
                updated['SCHEDULER_MINUTE'] = True
            else:
                new_lines.append(line)
        
        # Add missing settings
        if not updated['SCHEDULER_TIMEZONE']:
            new_lines.append(f'SCHEDULER_TIMEZONE={timezone}\n')
        if not updated['SCHEDULER_HOUR']:
            new_lines.append(f'SCHEDULER_HOUR={hour}\n')
        if not updated['SCHEDULER_MINUTE']:
            new_lines.append(f'SCHEDULER_MINUTE={minute}\n')
        
        with open(env_file, 'w') as f:
            f.writelines(new_lines)
        
        # Reload config
        from config import reload_config
        reload_config()
        
        # Restart scheduler with new settings
        sched = init_scheduler()
        was_running = sched.scheduler.running
        if was_running:
            sched.stop()
            import time
            time.sleep(1)
        
        # Reinitialize scheduler with new timezone
        global scheduler
        scheduler = None
        sched = init_scheduler()
        
        if was_running:
            sched.start()
        
        jobs = sched.scheduler.get_jobs()
        next_run = jobs[0].next_run_time if jobs else None
        
        return jsonify({
            'success': True,
            'message': f'Scheduler settings updated successfully. Next run: {next_run.strftime("%Y-%m-%d %H:%M:%S") if next_run else "N/A"}',
            'next_run': next_run.isoformat() if next_run else None
        })
    except Exception as e:
        import logging
        logger = logging.getLogger('zendesk_offloader')
        logger.error(f'Error updating scheduler settings: {str(e)}', exc_info=True)
        return jsonify({'success': False, 'message': f'Error updating scheduler: {str(e)}'}), 500

@app.route('/api/reset_admin_password', methods=['POST'])
@login_required
def reset_admin_password():
    """Reset admin password and send to Telegram (requires login)"""
    return _reset_admin_password_internal()

@app.route('/api/reset_admin_password_public', methods=['POST'])
def reset_admin_password_public():
    """Reset admin password from login page (public, no login required)"""
    # Check if Telegram is configured - this is required for security
    # Check database first, then environment
    db = get_db()
    telegram_configured = False
    try:
        telegram_token_setting = db.query(Setting).filter_by(key='TELEGRAM_BOT_TOKEN').first()
        telegram_chat_setting = db.query(Setting).filter_by(key='TELEGRAM_CHAT_ID').first()
        if telegram_token_setting and telegram_chat_setting and telegram_token_setting.value and telegram_chat_setting.value:
            telegram_configured = True
    finally:
        db.close()
    
    # If not in database, check environment
    if not telegram_configured:
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            return jsonify({
                'success': False,
                'message': 'Password reset is not available. Telegram is not configured. Please configure Telegram bot token and chat ID in settings.'
            }), 400
    
    return _reset_admin_password_internal()

def _save_admin_password(new_password):
    """Persist admin password to .env and database."""
    from config import BASE_DIR, reload_config
    env_file = BASE_DIR / '.env'
    env_lines = []
    if env_file.exists():
        with open(env_file, 'r') as f:
            env_lines = f.readlines()
    env_updated = False
    new_lines = []
    if env_lines:
        for line in env_lines:
            if line.strip().startswith('ADMIN_PASSWORD='):
                new_lines.append(f'ADMIN_PASSWORD={new_password}\n')
                env_updated = True
            else:
                new_lines.append(line)
    else:
        new_lines = []
    if not env_updated:
        new_lines.append(f'ADMIN_PASSWORD={new_password}\n')
    with open(env_file, 'w') as f:
        f.writelines(new_lines)
    db = get_db()
    try:
        setting = db.query(Setting).filter_by(key='ADMIN_PASSWORD').first()
        if setting:
            setting.value = new_password
            setting.updated_at = datetime.utcnow()
        else:
            setting = Setting(key='ADMIN_PASSWORD', value=new_password)
            db.add(setting)
        db.commit()
    finally:
        db.close()
    reload_config()
    return env_file

def _reset_admin_password_internal():
    """Reset admin password and send to Telegram"""
    try:
        from password_generator import generate_secure_password
        from telegram_reporter import TelegramReporter
        from config import BASE_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ADMIN_USERNAME
        import logging
        
        logger = logging.getLogger('zendesk_offloader')
        
        # Generate new password
        new_password = generate_secure_password(64)
        
        # Update password storage
        env_file = _save_admin_password(new_password)
        
        # Verify the new password was saved correctly by reading from .env
        # This ensures we're sending the actual new password, not any cached value
        saved_password = new_password
        if env_file.exists():
            with open(env_file, 'r') as f:
                for line in f:
                    if line.strip().startswith('ADMIN_PASSWORD='):
                        saved_password = line.split('=', 1)[1].strip()
                        break
        
        # Use the saved password (should be the same as new_password, but verify)
        password_to_send = saved_password if saved_password else new_password
        
        # Log the password reset for audit
        reset_by = session.get('user_name') or session.get('user_email') or session.get('username', 'Unknown')
        if not session.get('logged_in') and not session.get('user_email'):
            reset_by = 'Public (from login page)'
        logger.info(f"Admin password reset by {reset_by}")
        
        # Send password to Telegram
        # Check database for Telegram settings first (database takes priority)
        db_telegram = get_db()
        telegram_bot_token = None
        telegram_chat_id = None
        try:
            telegram_token_setting = db_telegram.query(Setting).filter_by(key='TELEGRAM_BOT_TOKEN').first()
            telegram_chat_setting = db_telegram.query(Setting).filter_by(key='TELEGRAM_CHAT_ID').first()
            if telegram_token_setting:
                telegram_bot_token = telegram_token_setting.value
            if telegram_chat_setting:
                telegram_chat_id = telegram_chat_setting.value
        finally:
            db_telegram.close()
        
        # If not in database, reload config and get from environment
        if not telegram_bot_token or not telegram_chat_id:
            from config import reload_config, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
            reload_config()
            telegram_bot_token = telegram_bot_token or TELEGRAM_BOT_TOKEN
            telegram_chat_id = telegram_chat_id or TELEGRAM_CHAT_ID
        
        telegram_sent = False
        if telegram_bot_token and telegram_chat_id:
            try:
                telegram_reporter = TelegramReporter(bot_token=telegram_bot_token, chat_id=telegram_chat_id)
                reset_source = reset_by if reset_by != 'Public (from login page)' else 'Login Page (Public)'
                message = f"""🔐 <b>Admin Password Reset</b>

<b>Username:</b> {ADMIN_USERNAME}
<b>New Password:</b> <code>{password_to_send}</code>

⚠️ <b>Important:</b> Save this password securely. It will not be shown again.

<i>Reset by: {reset_source}</i>
<i>Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</i>"""
                
                telegram_sent = telegram_reporter.send_message(message)
                
                if not telegram_sent:
                    logger.warning("Failed to send password to Telegram - check bot token and chat ID configuration")
            except Exception as e:
                logger.error(f"Error sending password to Telegram: {str(e)}", exc_info=True)
                telegram_sent = False
        else:
            logger.warning(f"Telegram not configured - bot_token: {bool(telegram_bot_token)}, chat_id: {bool(telegram_chat_id)}")
        
        if telegram_sent:
            return jsonify({
                'success': True,
                'message': 'Password reset successfully! New password has been sent to Telegram: https://t.me/rhcsolutions'
            })
        else:
            return jsonify({
                'success': True,
                'message': f'Password reset successfully! However, Telegram notification failed. New password: {password_to_send}',
                'warning': True
            })
            
    except Exception as e:
        import logging
        logger = logging.getLogger('zendesk_offloader')
        logger.error(f'Error resetting admin password: {str(e)}', exc_info=True)
        return jsonify({
            'success': False,
            'message': f'Error resetting password: {str(e)}'
        }), 500


# ── Global Tools Page ──────────────────────────────────────────────────────────

@app.route('/tools')
@login_required
def tools():
    """Network & infrastructure diagnostics tools page."""
    from config import (
        WASABI_ENDPOINT, WASABI_ACCESS_KEY, WASABI_SECRET_KEY, WASABI_BUCKET_NAME,
        TICKET_BACKUP_ENDPOINT, TICKET_BACKUP_BUCKET,
    )
    import re

    def _endpoint_host(ep):
        ep = ep or ''
        if not ep.startswith('http'):
            ep = 'https://' + ep
        m = re.search(r'https?://([^/]+)', ep)
        return m.group(1) if m else ep

    buckets = []
    if WASABI_ENDPOINT and WASABI_BUCKET_NAME:
        buckets.append({
            'label': f'Offload ({WASABI_BUCKET_NAME})',
            'host': _endpoint_host(WASABI_ENDPOINT),
            'bucket': WASABI_BUCKET_NAME,
            'endpoint': WASABI_ENDPOINT,
        })
    if TICKET_BACKUP_ENDPOINT and TICKET_BACKUP_BUCKET:
        host2 = _endpoint_host(TICKET_BACKUP_ENDPOINT)
        if not any(b['host'] == host2 and b['bucket'] == TICKET_BACKUP_BUCKET for b in buckets):
            buckets.append({
                'label': f'Backup ({TICKET_BACKUP_BUCKET})',
                'host': host2,
                'bucket': TICKET_BACKUP_BUCKET,
                'endpoint': TICKET_BACKUP_ENDPOINT,
            })
    return render_template('tools.html', buckets=buckets)


@app.route('/api/tools/ping')
@login_required
def tools_ping():
    """Stream ping results (10 packets) to a target host."""
    import subprocess, shlex
    from flask import Response, stream_with_context
    target = (request.args.get('host') or '').strip()
    if not target:
        return jsonify({'error': 'host required'}), 400

    # Sanitise: only allow hostname/IP chars
    import re
    if not re.match(r'^[a-zA-Z0-9.\-]+$', target):
        return jsonify({'error': 'invalid host'}), 400

    def generate():
        try:
            proc = subprocess.Popen(
                ['ping', '-c', '10', '-W', '3', target],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            for line in proc.stdout:
                yield line
            proc.wait()
            yield f'\n[exit code: {proc.returncode}]\n'
        except Exception as exc:
            yield f'Error: {exc}\n'

    return Response(stream_with_context(generate()), mimetype='text/plain')


@app.route('/api/tools/traceroute')
@login_required
def tools_traceroute():
    """Stream traceroute to a target host."""
    import subprocess, re
    from flask import Response, stream_with_context
    target = (request.args.get('host') or '').strip()
    if not target or not re.match(r'^[a-zA-Z0-9.\-]+$', target):
        return jsonify({'error': 'invalid host'}), 400

    def generate():
        try:
            proc = subprocess.Popen(
                ['traceroute', '-w', '3', '-m', '20', target],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            for line in proc.stdout:
                yield line
            proc.wait()
            yield f'\n[exit code: {proc.returncode}]\n'
        except Exception as exc:
            yield f'Error: {exc}\n'

    return Response(stream_with_context(generate()), mimetype='text/plain')


@app.route('/api/tools/dns')
@login_required
def tools_dns():
    """DNS lookup using dig."""
    import subprocess, re
    from flask import Response, stream_with_context
    target = (request.args.get('host') or '').strip()
    rtype = (request.args.get('type') or 'A').strip().upper()
    if not target or not re.match(r'^[a-zA-Z0-9.\-]+$', target):
        return jsonify({'error': 'invalid host'}), 400
    if rtype not in ('A', 'AAAA', 'MX', 'TXT', 'NS', 'CNAME', 'PTR', 'SOA'):
        rtype = 'A'

    def generate():
        try:
            proc = subprocess.Popen(
                ['dig', '+noall', '+answer', '+stats', rtype, target],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            for line in proc.stdout:
                yield line
            proc.wait()
        except Exception as exc:
            yield f'Error: {exc}\n'

    return Response(stream_with_context(generate()), mimetype='text/plain')


@app.route('/api/tools/speedtest')
@login_required
def tools_speedtest():
    """
    Wasabi speed test: download Rocky Linux ISO (~976 MB), then upload to bucket,
    reporting throughput in real-time via SSE-style plain text stream.
    """
    import re, time, threading, tempfile, os
    from flask import Response, stream_with_context
    from config import WASABI_ACCESS_KEY, WASABI_SECRET_KEY

    bucket_id = (request.args.get('bucket') or '0').strip()
    from config import (
        WASABI_ENDPOINT, WASABI_BUCKET_NAME,
        TICKET_BACKUP_ENDPOINT, TICKET_BACKUP_BUCKET,
    )

    def _endpoint_host(ep):
        if not ep.startswith('http'):
            ep = 'https://' + ep
        return ep

    bucket_configs = []
    if WASABI_ENDPOINT and WASABI_BUCKET_NAME:
        bucket_configs.append((WASABI_BUCKET_NAME, _endpoint_host(WASABI_ENDPOINT)))
    if TICKET_BACKUP_ENDPOINT and TICKET_BACKUP_BUCKET:
        bucket_configs.append((TICKET_BACKUP_BUCKET, _endpoint_host(TICKET_BACKUP_ENDPOINT)))

    try:
        idx = int(bucket_id)
        if idx >= len(bucket_configs):
            idx = 0
    except ValueError:
        idx = 0

    bucket_name, endpoint = bucket_configs[idx] if bucket_configs else (WASABI_BUCKET_NAME, _endpoint_host(WASABI_ENDPOINT))

    ISO_URL = 'https://download.rockylinux.org/pub/rocky/10/isos/x86_64/Rocky-10.1-x86_64-minimal.iso'
    TEST_KEY = '__speedtest_rocky_minimal.iso'

    def generate():
        import boto3, requests as req_lib

        yield f'=== Wasabi Speed Test: {bucket_name} ({endpoint}) ===\n'
        yield f'ISO: {ISO_URL}\n\n'

        # ── Phase 1: Download ISO from Rocky CDN ──────────────────────
        yield '--- Phase 1: Download from Rocky CDN ---\n'
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.iso')
        try:
            t0 = time.time()
            downloaded = 0
            chunk_size = 4 * 1024 * 1024  # 4 MB chunks
            last_report = 0

            with req_lib.get(ISO_URL, stream=True, timeout=30) as r:
                r.raise_for_status()
                total_size = int(r.headers.get('content-length', 0))
                yield f'File size: {total_size / 1048576:.1f} MB\n'

                for chunk in r.iter_content(chunk_size=chunk_size):
                    if chunk:
                        tmp.write(chunk)
                        downloaded += len(chunk)
                        elapsed = time.time() - t0
                        speed_mb = (downloaded / elapsed / 1048576) if elapsed > 0 else 0
                        pct = (downloaded / total_size * 100) if total_size else 0
                        if downloaded - last_report >= 50 * 1024 * 1024:  # report every 50 MB
                            yield (
                                f'  Downloaded: {downloaded/1048576:.0f} MB / {total_size/1048576:.0f} MB'
                                f'  ({pct:.0f}%)  {speed_mb:.1f} MB/s\n'
                            )
                            last_report = downloaded

            elapsed_dl = time.time() - t0
            speed_dl = downloaded / elapsed_dl / 1048576
            yield f'\nDownload complete: {downloaded/1048576:.1f} MB in {elapsed_dl:.1f}s = {speed_dl:.2f} MB/s\n\n'
            tmp.flush()

            # ── Phase 2: Upload to Wasabi ──────────────────────────────
            yield '--- Phase 2: Upload to Wasabi ---\n'
            yield f'Bucket: {bucket_name}  Key: {TEST_KEY}\n'

            s3 = boto3.client(
                's3',
                endpoint_url=endpoint,
                aws_access_key_id=WASABI_ACCESS_KEY,
                aws_secret_access_key=WASABI_SECRET_KEY,
            )

            file_size = os.path.getsize(tmp.name)
            upload_progress = {'bytes': 0, 'last_report': 0, 'start': time.time()}

            def _progress_cb(bytes_transferred):
                upload_progress['bytes'] += bytes_transferred

            t1 = time.time()
            # Use multipart via transfer config for progress
            from boto3.s3.transfer import TransferConfig
            config = TransferConfig(multipart_chunksize=8 * 1024 * 1024, max_concurrency=4)

            # We can't yield inside callback, so upload synchronously and report after
            yield f'Uploading {file_size/1048576:.1f} MB ...\n'
            with open(tmp.name, 'rb') as fh:
                s3.upload_fileobj(fh, bucket_name, TEST_KEY, Config=config,
                                  Callback=_progress_cb)
            elapsed_ul = time.time() - t1
            speed_ul = file_size / elapsed_ul / 1048576
            yield f'Upload complete: {file_size/1048576:.1f} MB in {elapsed_ul:.1f}s = {speed_ul:.2f} MB/s\n\n'

            # ── Phase 3: Download back from Wasabi ─────────────────────
            yield '--- Phase 3: Download from Wasabi ---\n'
            t2 = time.time()
            dl_bytes = 0
            obj = s3.get_object(Bucket=bucket_name, Key=TEST_KEY)
            body = obj['Body']
            while True:
                chunk = body.read(4 * 1024 * 1024)
                if not chunk:
                    break
                dl_bytes += len(chunk)
            elapsed_dl2 = time.time() - t2
            speed_dl2 = dl_bytes / elapsed_dl2 / 1048576
            yield f'Download complete: {dl_bytes/1048576:.1f} MB in {elapsed_dl2:.1f}s = {speed_dl2:.2f} MB/s\n\n'

            # ── Phase 4: Cleanup ───────────────────────────────────────
            try:
                s3.delete_object(Bucket=bucket_name, Key=TEST_KEY)
                yield f'Cleanup: {TEST_KEY} deleted from bucket.\n\n'
            except Exception as ce:
                yield f'Cleanup warning: {ce}\n\n'

            yield '=== Summary ===\n'
            yield f'  CDN download:     {speed_dl:.2f} MB/s\n'
            yield f'  Wasabi upload:    {speed_ul:.2f} MB/s\n'
            yield f'  Wasabi download:  {speed_dl2:.2f} MB/s\n'

        except Exception as exc:
            yield f'\nERROR: {exc}\n'
        finally:
            tmp.close()
            try:
                os.unlink(tmp.name)
            except Exception:
                pass

    return Response(stream_with_context(generate()), mimetype='text/plain')

if __name__ == '__main__':
    # Initialize database
    from database import init_db
    init_db()
    
    # Start scheduler
    sched = init_scheduler()
    sched.start()
    
    # Configure Flask logging - suppress favicon and static file noise
    # Keep error logging but suppress INFO level access logs
    import logging
    werkzeug_log = logging.getLogger('werkzeug')
    werkzeug_log.setLevel(logging.ERROR)  # Only show errors, not access logs
    
    # Run Flask app
    app.run(host=ADMIN_PANEL_HOST, port=ADMIN_PANEL_PORT, debug=False)

