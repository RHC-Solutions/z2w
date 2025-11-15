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
from config import ADMIN_PANEL_PORT, ADMIN_PANEL_HOST, SECRET_KEY, ADMIN_USERNAME, ADMIN_PASSWORD
import os
from functools import wraps

app = Flask(__name__)
app.secret_key = SECRET_KEY

# Global scheduler instance
scheduler = None

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
        if not session.get('logged_in'):
            return redirect(url_for('login', next=request.path))
        return view_func(*args, **kwargs)
    return wrapper

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
    """Admin login"""
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['logged_in'] = True
            session['username'] = username
            next_url = request.args.get('next') or url_for('index')
            return redirect(next_url)
        flash('Invalid credentials', 'error')
        return render_template('login.html')
    # If already logged in, go to dashboard
    if session.get('logged_in'):
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    """End session"""
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
        ).limit(10).all()
        
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
            for key, value in request.form.items():
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
                
            except Exception as e:
                flash(f'Settings saved to database but failed to update .env file: {str(e)}', 'warning')
            
            flash('Settings updated successfully', 'success')
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

