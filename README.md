# Zendesk to Wasabi B2 Attachment Offloader

Automated system to offload attachments from Zendesk tickets to Wasabi B2 cloud storage with daily scheduling and email reporting.

## Features

### Core Offloading Features
- ✅ **Automatic Ticket Processing**: Fetches all tickets from Zendesk and processes only new tickets
- ✅ **Smart Tracking**: Tracks processed tickets in SQLite database to avoid reprocessing
- ✅ **Attachment Handling**: Downloads and processes both regular attachments and inline images from ticket comments
- ✅ **Wasabi B2 Integration**: Uploads all attachments to Wasabi B2 cloud storage (S3-compatible)
- ✅ **Zendesk Cleanup**: Replaces attachments in Zendesk comments with Wasabi links and deletes original attachments
- ✅ **Inline Image Support**: Handles inline images embedded in comments, replaces them with Wasabi links
- ✅ **File Organization**: Organizes files by date in folders (`YYYYMMDD/`)
- ✅ **Smart Naming**: Renames files with ticket ID and date prefix (`ticketID_YYYYMMDD_filename`)
- ✅ **Ticket Marking**: Automatically marks tickets as read after successful processing

### Scheduling & Automation
- ✅ **Configurable Scheduling**: Daily execution at configurable time (default: 00:00 GMT)
- ✅ **Manual Trigger**: Run offload jobs on-demand from admin panel
- ✅ **Scheduler Control**: Start/stop scheduler from web interface
- ✅ **Automatic Log Archiving**: Daily archiving of old logs (configurable retention)

### Multi-Channel Reporting
- ✅ **Email Reports**: Detailed HTML-formatted email reports with statistics and file lists
- ✅ **Telegram Reports**: Send formatted reports to Telegram channels/bots
- ✅ **Slack Reports**: Send rich formatted reports to Slack via webhooks
- ✅ **Multi-Channel Support**: Configure and use multiple reporting channels simultaneously
- ✅ **Error Reporting**: Comprehensive error tracking and reporting in all channels

### Web Admin Panel
- ✅ **Secure Login**: Password-protected admin interface
- ✅ **Dashboard**: Real-time statistics, recent logs, and scheduler status
- ✅ **Settings Management**: Web-based configuration with database and .env file synchronization
- ✅ **Connection Testing**: Test Zendesk and Wasabi connections directly from the interface
- ✅ **Ticket Browser**: View all processed tickets with pagination and file links
- ✅ **Log Viewer**: Browse detailed offload execution logs with file information
- ✅ **HTTPS Support**: Optional SSL/HTTPS configuration for secure access
- ✅ **Responsive Design**: Modern, user-friendly web interface

### Database & Logging
- ✅ **SQLite Database**: Lightweight, file-based database for tracking
- ✅ **Processed Tickets Tracking**: Stores ticket IDs, status, attachment counts, and error messages
- ✅ **Offload Logs**: Detailed execution logs with timestamps, statistics, and file information
- ✅ **Settings Storage**: Database-backed settings with .env file synchronization
- ✅ **Automatic Migration**: Database schema migration for seamless updates
- ✅ **File Path Storage**: Stores Wasabi S3 keys for easy file retrieval
- ✅ **Application Logging**: Comprehensive logging with automatic log archiving

### Configuration & Security
- ✅ **Environment Variables**: Flexible .env file configuration
- ✅ **Database Settings**: Settings stored in database with .env sync
- ✅ **HTTPS/SSL Support**: Optional SSL certificate configuration
- ✅ **Session Management**: Secure Flask session handling
- ✅ **Error Handling**: Comprehensive error tracking and graceful failure handling

## Installation

1. **Clone or download this repository**

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables:**
   - Copy `.env.example` to `.env`
   - Fill in all required configuration values

## Configuration

Create a `.env` file with the following settings:

### Zendesk Configuration
- `ZENDESK_SUBDOMAIN`: Your Zendesk subdomain (e.g., "mycompany" for mycompany.zendesk.com)
- `ZENDESK_EMAIL`: Your Zendesk account email
- `ZENDESK_API_TOKEN`: Your Zendesk API token (generate in Zendesk Admin > Apps and integrations > APIs > Zendesk API)

### Wasabi B2 Configuration
- `WASABI_ENDPOINT`: Wasabi endpoint URL (e.g., https://s3.wasabisys.com)
- `WASABI_ACCESS_KEY`: Your Wasabi access key
- `WASABI_SECRET_KEY`: Your Wasabi secret key
- `WASABI_BUCKET_NAME`: Target bucket name

### Email Configuration
- `SMTP_SERVER`: SMTP server address (default: smtp.gmail.com)
- `SMTP_PORT`: SMTP port (default: 587)
- `SMTP_USERNAME`: SMTP username/email
- `SMTP_PASSWORD`: SMTP password or app password
- `REPORT_EMAIL`: Email address to receive reports (default: it@go4rex.com)

### Telegram Configuration (Optional)
- `TELEGRAM_BOT_TOKEN`: Your Telegram bot token (for Telegram reports)
- `TELEGRAM_CHAT_ID`: Your Telegram chat ID or channel ID

### Slack Configuration (Optional)
- `SLACK_WEBHOOK_URL`: Your Slack webhook URL (for Slack reports)

### Scheduler Configuration
- `SCHEDULER_TIMEZONE`: Timezone for scheduler (default: UTC)
- `SCHEDULER_HOUR`: Hour to run scheduled job (default: 0)
- `SCHEDULER_MINUTE`: Minute to run scheduled job (default: 0)

### Admin Panel
- `ADMIN_PANEL_PORT`: Port for admin panel (default: 5000)
- `ADMIN_PANEL_HOST`: Host for admin panel (default: 0.0.0.0)
- `SECRET_KEY`: Secret key for Flask sessions (change in production!)

### SSL/HTTPS Configuration (Optional)
- `SSL_CERT_PATH`: Path to SSL certificate file (e.g., `cert.pem`)
- `SSL_KEY_PATH`: Path to SSL private key file (e.g., `key.pem`)

If both `SSL_CERT_PATH` and `SSL_KEY_PATH` are set and the files exist, the app will run with HTTPS. Otherwise, it will use HTTP.

## Usage

### Start the Application

```bash
python main.py
```

The application will:
1. Initialize the database
2. Start the scheduler (runs daily at 00:00 GMT)
3. Launch the admin panel web interface

### Access Admin Panel

Open your browser and navigate to:
```
http://localhost:5000
```

Or if HTTPS is configured:
```
https://localhost:5000
```

**Note**: If using self-signed certificates, your browser will show a security warning. This is normal for development/testing. For production, use certificates from a trusted Certificate Authority (CA).

### Admin Panel Features

#### Dashboard
- View total processed tickets and attachments
- See recent offload logs
- Check scheduler status and next run time
- Quick actions: Run Now, Start/Stop Scheduler

#### Settings Page
- Configure all Zendesk credentials
- Configure all Wasabi B2 credentials
- Configure email, Telegram, and Slack reporting
- Test connections to Zendesk and Wasabi
- Settings are saved to both database and .env file
- Real-time configuration updates

#### Tickets Page
- Browse all processed tickets with pagination
- View ticket details: ID, processing date, attachment count, status
- Access direct links to uploaded files in Wasabi
- Filter and search capabilities
- View error messages for failed tickets

#### Logs Page
- View detailed execution logs for each offload run
- See statistics: tickets processed, attachments uploaded, errors
- Access links to all uploaded files
- View full execution details and error information
- Pagination for browsing historical logs

### Manual Operations

From the admin panel dashboard, you can:
- **Run Now**: Manually trigger an offload job immediately
- **Start Scheduler**: Start the automatic scheduler
- **Stop Scheduler**: Stop the automatic scheduler
- **View Status**: See if scheduler is running and next scheduled run time

## How It Works

### 1. Ticket Processing
- Fetches all tickets from Zendesk using the Zendesk API
- Filters to only process new tickets (not already in database)
- For each new ticket:
  - Downloads all regular attachments from comments
  - Downloads all inline images embedded in comments
  - Processes attachments and images separately to avoid duplicates

### 2. File Upload & Organization
- Uploads each attachment/image to Wasabi B2 storage
- Creates date-based folders: `YYYYMMDD/`
- Renames files with format: `ticketID_YYYYMMDD_original_filename`
- Stores original content type for proper file handling
- Generates presigned URLs for file access (1-year expiration)

### 3. Zendesk Cleanup
- Replaces attachment references in Zendesk comments with Wasabi links
- Replaces inline image references with Wasabi links
- Deletes original attachments from Zendesk after successful upload
- Marks tickets as read after processing

### 4. Tracking & Logging
- Records processed tickets in SQLite database with:
  - Ticket ID, processing timestamp, attachment count
  - Status (processed/error), error messages
  - Wasabi file paths (S3 keys) for easy retrieval
- Creates detailed offload logs with:
  - Run date, tickets processed, attachments uploaded
  - Error count, status, and full execution details
  - All S3 keys for uploaded files

### 5. Scheduling
- Runs automatically at configured time (default: daily at 00:00 GMT)
- Can be manually triggered from admin panel
- Scheduler can be started/stopped from web interface
- Automatic log archiving runs daily after offload job

### 6. Multi-Channel Reporting
- Sends reports to all configured channels after each run:
  - **Email**: HTML-formatted report with statistics, file lists, and errors
  - **Telegram**: Formatted message with emojis and summary
  - **Slack**: Rich formatted message with attachments and fields
- Reports include:
  - Run date and time
  - Tickets found and processed
  - Attachments uploaded and deleted
  - Error details (if any)
  - Ticket-by-ticket breakdown

## File Structure

```
z2w1/
├── main.py                 # Main entry point
├── config.py              # Configuration management
├── database.py             # Database models
├── zendesk_client.py       # Zendesk API client
├── wasabi_client.py        # Wasabi B2 client
├── offloader.py            # Main offload logic
├── scheduler.py            # Scheduled job runner
├── email_reporter.py       # Email reporting
├── telegram_reporter.py    # Telegram reporting
├── slack_reporter.py       # Slack reporting
├── admin_panel.py          # Flask web interface
├── logger_config.py        # Logging configuration
├── generate_ssl_cert.py    # SSL certificate generator (optional)
├── templates/              # HTML templates
│   ├── base.html
│   ├── dashboard.html
│   ├── settings.html
│   ├── tickets.html
│   └── logs.html
├── requirements.txt        # Python dependencies
├── .env.example           # Environment variables template
└── README.md              # This file
```

## Database

The application uses SQLite database (`tickets.db`) to track:

### processed_tickets Table
- `id`: Primary key
- `ticket_id`: Zendesk ticket ID (unique, indexed)
- `processed_at`: Timestamp when ticket was processed
- `attachments_count`: Number of attachments uploaded
- `status`: Processing status (processed/error)
- `error_message`: Error details if processing failed
- `wasabi_files`: JSON array of Wasabi S3 keys for uploaded files

### offload_logs Table
- `id`: Primary key
- `run_date`: Timestamp of offload execution (indexed)
- `tickets_processed`: Number of tickets processed
- `attachments_uploaded`: Number of attachments uploaded
- `errors_count`: Number of errors encountered
- `status`: Execution status (completed/completed_with_errors)
- `report_sent`: Whether reports were sent successfully
- `details`: JSON string with full execution details and file information

### settings Table
- `id`: Primary key
- `key`: Setting key (unique)
- `value`: Setting value
- `description`: Optional description
- `updated_at`: Last update timestamp

Settings are synchronized between database and .env file for flexibility.

## Troubleshooting

### Connection Issues

Use the "Test Connection" buttons in the Settings page to verify:
- Zendesk API credentials
- Wasabi B2 credentials

### Email Not Sending

- Verify SMTP credentials
- For Gmail, use an App Password instead of regular password
- Check firewall/network settings

### Scheduler Not Running

- Check scheduler status on dashboard
- Verify system timezone is correct
- Check logs for errors

## HTTPS Setup

### Option 1: Use Your Own Certificates

1. Obtain SSL certificates from a Certificate Authority (CA) or use your existing certificates
2. Add to your `.env` file:
   ```
   SSL_CERT_PATH=/path/to/cert.pem
   SSL_KEY_PATH=/path/to/key.pem
   ```
3. Restart the application

### Option 2: Generate Self-Signed Certificates (Development Only)

For local development and testing, you can generate self-signed certificates:

1. Install the cryptography library (if not already installed):
   ```bash
   pip install cryptography
   ```

2. Run the certificate generator:
   ```bash
   python generate_ssl_cert.py
   ```

3. This will create `cert.pem` and `key.pem` in the current directory

4. Add to your `.env` file:
   ```
   SSL_CERT_PATH=cert.pem
   SSL_KEY_PATH=key.pem
   ```

5. Restart the application

**Important**: Self-signed certificates are for development only. Browsers will show security warnings. For production, use certificates from a trusted CA.

## Security Notes

- Change `SECRET_KEY` in production
- Keep `.env` file secure (don't commit to version control)
- Use strong API tokens and passwords
- Enable HTTPS in production using trusted SSL certificates
- Consider running behind a reverse proxy (nginx, Apache) with HTTPS for additional security

## License

This project is provided as-is for internal use.


