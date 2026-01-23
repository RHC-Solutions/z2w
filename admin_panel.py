"""
Admin panel for managing settings and monitoring
"""
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session
from datetime import datetime
from sqlalchemy import func
from database import get_db, Setting, ProcessedTicket, OffloadLog
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
                             next_run=next_run)
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
            scheduler_keys = {'SCHEDULER_TIMEZONE', 'SCHEDULER_HOUR', 'SCHEDULER_MINUTE'}
            
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
        per_page = 50
        
        # Manual pagination
        total = db.query(ProcessedTicket).count()
        tickets_query = db.query(ProcessedTicket).order_by(
            ProcessedTicket.processed_at.desc()
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
        
        return render_template('tickets.html', tickets=tickets)
    finally:
        db.close()

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

@app.route('/api/test_connection', methods=['POST'])
@login_required
def test_connection():
    """Test Zendesk and Wasabi connections"""
    connection_type = request.json.get('type')
    
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
    
    return jsonify({'success': False, 'message': 'Invalid connection type'})

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

