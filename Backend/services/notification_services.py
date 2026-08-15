# services/notification_services.py
import os
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr, parseaddr
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
logger = logging.getLogger(__name__)

class NotificationService:
    """Handles Email alerts for AgriGuard Agent"""
    
    def __init__(self):
        # Email configuration
        self.email_host = os.getenv('EMAIL_HOST', 'smtp.gmail.com')
        self.email_port = int(os.getenv('EMAIL_PORT', 587))
        self.email_user = os.getenv('EMAIL_USER')
        self.email_password = os.getenv('EMAIL_PASSWORD')
        self.email_from_name = (os.getenv('EMAIL_FROM_NAME') or 'AgriGuard').strip()
        # Public From address (what recipients see). SMTP still logs in with EMAIL_USER.
        raw_from = (
            os.getenv('EMAIL_FROM')
            or 'noreply@agriguard.co.za'
        ).strip()
        # Allow EMAIL_FROM="AgriGuard <noreply@agriguard.co.za>" or just the address
        name, addr = parseaddr(raw_from)
        self.email_from_addr = addr or 'noreply@agriguard.co.za'
        self.email_from = formataddr((
            name or self.email_from_name,
            self.email_from_addr,
        ))
        
        # Remove spaces from app password if present
        if self.email_password:
            self.email_password = self.email_password.replace(' ', '')
        
        # Validate configuration
        if not self.email_user or not self.email_password:
            logger.warning("⚠️ Email credentials not configured in .env file")
        else:
            logger.info("✅ Email service initialized")
    
    def send_alert(self, email, animal_tag, anomaly_type, location, severity="Medium", details=""):
        """
        Send email alert for anomaly detection
        
        Args:
            email (str): Recipient's email address
            animal_tag (str): Animal identification tag
            anomaly_type (str): Type of anomaly detected
            location (str): Location of the animal
            severity (str): High, Medium, or Low
            details (str): Additional details about the anomaly
        
        Returns:
            bool: True if email sent successfully, False otherwise
        """
        try:
            if not email:
                logger.error("❌ No email address provided")
                return False
            
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # Create email subject
            subject = f"🚨 AGRIGUARD ALERT: {animal_tag} - {anomaly_type}"
            
            # Email body
            body = f"""
AGRIGUARD ANOMALY ALERT
{'='*50}

Dear Farmer,

An anomaly has been detected in your livestock. Please review the details below:

Animal Tag:     {animal_tag}
Anomaly Type:   {anomaly_type}
Location:       {location}
Severity:       {severity}
Time:           {timestamp}
{'' if not details else f'Details:        {details}'}

Recommended Actions:
1. Log into AgriGuard system immediately
2. Review the animal's recent activity
3. Check on the animal in person if possible
4. Update the animal's status in the system

View Animal: http://localhost:5000/animals/{animal_tag}
View Dashboard: http://localhost:5000/dashboard

{'='*50}
This is an automated alert from the AgriGuard System.
© {datetime.now().year} AgriGuard - Livestock Monitoring System
"""
            
            logger.info(f"📧 Sending alert to: {email}")
            
            # Send the email
            result = self._send_email(email, subject, body)
            
            if result:
                logger.info(f"✅ Alert sent to {email} for animal {animal_tag}")
            else:
                logger.warning(f"❌ Failed to send alert to {email}")
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Alert failed: {str(e)}")
            return False
    
    def send_password_reset(self, email, reset_link, expires_hours=1):
        """Send a password-reset email with a one-time link."""
        if not email or not reset_link:
            logger.error("Password reset email missing recipient or link")
            return False

        subject = "AgriGuard password reset"
        body = f"""AgriGuard Password Reset
{'=' * 50}

We received a request to reset the password for this account.

Open this link to choose a new password (valid for {expires_hours} hour(s)):

{reset_link}

If you did not request this, you can ignore this email.
Your password will stay the same.

{'=' * 50}
This is an automated message from AgriGuard.
"""
        logger.info("Sending password reset email to %s", email)
        return self._send_email(email, subject, body)

    def send_email_verification(self, email, verify_link, first_name="", expires_hours=24):
        """Send account email-verification link."""
        if not email or not verify_link:
            logger.error("Verification email missing recipient or link")
            return False

        name = first_name or "there"
        subject = "Verify your AgriGuard account"
        body = f"""AgriGuard Email Verification
{'=' * 50}

Hi {name},

Thanks for registering with AgriGuard.
Please verify your email address by opening this link
(valid for {expires_hours} hour(s)):

{verify_link}

If you did not create an AgriGuard account, you can ignore this email.

{'=' * 50}
This is an automated message from AgriGuard.
"""
        logger.info("Sending verification email to %s", email)
        return self._send_email(email, subject, body)
    
    def send_email_alert(self, email, animal_tag, **kwargs):
        """
        Alias for send_alert() - keeps compatibility with app.py
        
        Args:
            email (str): Recipient's email address
            animal_tag (str): Animal identification tag
            **kwargs: Additional parameters (anomaly_type, location, severity, details)
        
        Returns:
            bool: True if email sent successfully, False otherwise
        """
        anomaly_type = kwargs.get('anomaly_type', 'unusual behavior')
        location = kwargs.get('location', 'unknown location')
        severity = kwargs.get('severity', 'Medium')
        details = kwargs.get('details', '')
        
        return self.send_alert(
            email=email,
            animal_tag=animal_tag,
            anomaly_type=anomaly_type,
            location=location,
            severity=severity,
            details=details
        )
    
    def _send_email(self, to_email, subject, body):
        """Internal method to send email via SMTP"""
        try:
            # Validate credentials
            if not self.email_user or not self.email_password:
                logger.error("❌ Email credentials not configured")
                return False
            
            # Create message
            msg = MIMEMultipart()
            msg['From'] = self.email_from
            msg['To'] = to_email
            msg['Subject'] = subject
            
            # Attach body
            msg.attach(MIMEText(body, 'plain'))
            
            # Send via SMTP
            with smtplib.SMTP(self.email_host, self.email_port) as server:
                server.starttls()
                server.login(self.email_user, self.email_password)
                server.send_message(msg)
            
            return True
            
        except smtplib.SMTPAuthenticationError:
            logger.error("❌ Authentication failed - Check email and password in .env")
            return False
        except Exception as e:
            logger.error(f"❌ Email failed: {str(e)}")
            return False
