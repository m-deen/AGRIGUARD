# services/notification_services.py
import os
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
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
        self.email_from = os.getenv('EMAIL_FROM', self.email_user)
        
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
© 2024 AgriGuard - Livestock Monitoring System
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
    
    def send_test_email(self, email):
        """Send a test email to verify configuration"""
        return self.send_alert(
            email=email,
            animal_tag="TEST123",
            anomaly_type="Test Alert",
            location="Test Location",
            severity="Low",
            details="This is a test email from AgriGuard system."
        )
    
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
    
    # ============ FIXED METHOD ============
    def send_anomaly_alerts(self, user, animal_tag, anomaly_type, speed, lat, lon):
        """
        Send alerts for anomaly detection (called from simulate_gps)
        
        Args:
            user (dict): User object with 'email' and 'first_name'
            animal_tag (str): Animal identification tag
            anomaly_type (str): Type of anomaly detected
            speed (float): Speed of the animal
            lat (float): Latitude
            lon (float): Longitude
        
        Returns:
            bool: True if email sent successfully
        """
        email = user.get('email')
        if not email:
            return False
        
        return self.send_alert(
            email=email,
            animal_tag=animal_tag,
            anomaly_type=anomaly_type or 'GPS Anomaly',
            location=f"Lat: {lat:.6f}, Lon: {lon:.6f}",
            severity="High",
            details=f"Speed: {speed:.1f} km/h - GPS anomaly detected during simulation"
        )
    # =====================================
    
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
    
    def send_bulk_alerts(self, recipients, animal_tag, anomaly_type, location, severity="Medium", details=""):
        """
        Send alerts to multiple recipients
        
        Args:
            recipients (list): List of email addresses
            animal_tag (str): Animal identification tag
            anomaly_type (str): Type of anomaly detected
            location (str): Location of the animal
            severity (str): High, Medium, or Low
            details (str): Additional details
        
        Returns:
            dict: Summary of sent alerts
        """
        results = {
            'total': len(recipients),
            'sent': 0,
            'failed': 0,
            'failed_emails': []
        }
        
        for email in recipients:
            success = self.send_alert(
                email=email,
                animal_tag=animal_tag,
                anomaly_type=anomaly_type,
                location=location,
                severity=severity,
                details=details
            )
            
            if success:
                results['sent'] += 1
            else:
                results['failed'] += 1
                results['failed_emails'].append(email)
        
        logger.info(f"📊 Bulk alerts: {results['sent']} sent, {results['failed']} failed")
        return results