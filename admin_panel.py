"""
Admin panel for managing settings and monitoring
"""
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session
from datetime import datetime
from sqlalchemy import func, or_, cast, String, asc, desc
from database import get_db, Setting, ProcessedTicket, OffloadLog, ZendeskTicketCache, ZendeskStorageSnapshot
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

def init_scheduler():
    """Initialize scheduler"""
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

@app.route('/')
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
                    next_run = job.next_run_time.strftime('%Y-%m-%d %H:%M UTC')
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

        # ── Plan limit from settings ────────────────
        limit_row = db.query(Setting).filter_by(key='ZENDESK_STORAGE_LIMIT_GB').first()
        plan_limit_gb = 0.0
        if limit_row and limit_row.value:
            try:
                plan_limit_gb = float(limit_row.value)
            except (ValueError, TypeError):
                pass

        # ── Wasabi stats ────────────────────────────
        wasabi_stats = None
        try:
            from config import WASABI_ENDPOINT, WASABI_ACCESS_KEY, WASABI_SECRET_KEY, WASABI_BUCKET_NAME
            settings_dict = {s.key: s.value for s in db.query(Setting).all()}
            w_endpoint = (settings_dict.get('WASABI_ENDPOINT') or WASABI_ENDPOINT or '').strip()
            w_access = settings_dict.get('WASABI_ACCESS_KEY') or WASABI_ACCESS_KEY
            w_secret = settings_dict.get('WASABI_SECRET_KEY') or WASABI_SECRET_KEY
            w_bucket = settings_dict.get('WASABI_BUCKET_NAME') or WASABI_BUCKET_NAME
            if all([w_endpoint, w_access, w_secret, w_bucket]):
                if not w_endpoint.startswith('http'):
                    w_endpoint = f'https://{w_endpoint}'
                wc = WasabiClient(endpoint=w_endpoint, access_key=w_access,
                                  secret_key=w_secret, bucket_name=w_bucket)
                wasabi_stats = wc.get_storage_stats()
        except Exception:
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
            offloaded_bytes=int(wasabi_stats['total_bytes']) if wasabi_stats and wasabi_stats.get('total_bytes') else 0,
            offloaded_tickets=offloaded_tickets,
            tickets_with_files=tickets_with_files,
            plan_limit_gb=plan_limit_gb,
            wasabi_stats=wasabi_stats,
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
        per_page = 20
        
        # Manual pagination
        total = db.query(OffloadLog).count()
        logs_query = db.query(OffloadLog).order_by(
            OffloadLog.run_date.desc()
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
        
        return render_template('logs.html', logs=logs)
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
            reload_config()
            from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
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

