# Zendesk to Wasabi B2 Attachment Offloader

Automated system to offload attachments from Zendesk tickets to Wasabi B2 cloud storage with daily scheduling and email reporting.

## Features

- ✅ Automatically fetches all tickets from Zendesk
- ✅ Processes only new tickets (tracks processed tickets in database)
- ✅ Marks tickets as read after processing
- ✅ Uploads attachments to Wasabi B2 with date-based folders (YYYYMMDD)
- ✅ Renames attachments with ticket ID prefix (ticketID_filename)
- ✅ Scheduled daily execution at 00:00 GMT
- ✅ Detailed email reports sent to configured address
- ✅ Web-based admin panel for settings and monitoring

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

### Telegram Configuration
- `Bot Token`: Get your bot token from @BotFather on Telegram
- `Chat ID`: Your Telegram chat ID or channel ID

### Slack Configuration
- `Webhook URL`: Create an Incoming Webhook in your Slack workspace

### Admin Panel
- `ADMIN_PANEL_PORT`: Port for admin panel (default: 5000)
- `ADMIN_PANEL_HOST`: Host for admin panel (default: 0.0.0.0)
- `SECRET_KEY`: Secret key for Flask sessions (change in production!)

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

### Admin Panel Features

- **Dashboard**: View statistics, recent logs, and scheduler status
- **Settings**: Configure all connection settings and test connections
- **Tickets**: Browse processed tickets
- **Logs**: View detailed offload execution logs

### Manual Operations

From the admin panel dashboard, you can:
- **Run Now**: Manually trigger an offload job
- **Start/Stop Scheduler**: Control the automatic scheduler

## How It Works

1. **Ticket Processing**:
   - Fetches all tickets from Zendesk
   - Filters to only process new tickets (not in database)
   - For each new ticket, downloads all attachments

2. **File Organization**:
   - Creates date-based folders in Wasabi: `YYYYMMDD/`
   - Renames files with ticket ID prefix: `ticketID_original_filename`
   - Uploads to Wasabi B2 storage

3. **Tracking**:
   - Records processed tickets in SQLite database
   - Marks tickets as read in Zendesk (adds tag)
   - Logs all operations for audit trail

4. **Scheduling**:
   - Runs automatically every day at 00:00 GMT
   - Can be manually triggered from admin panel

5. **Reporting**:
   - Sends detailed HTML email report after each run
   - Includes statistics, file list, and any errors

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
├── admin_panel.py          # Flask web interface
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
- **processed_tickets**: All processed tickets with status
- **offload_logs**: Execution logs with statistics
- **settings**: Application settings (optional, can use .env)

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

## Security Notes

- Change `SECRET_KEY` in production
- Keep `.env` file secure (don't commit to version control)
- Use strong API tokens and passwords
- Consider running behind a reverse proxy with HTTPS

## License

This project is provided as-is for internal use.


