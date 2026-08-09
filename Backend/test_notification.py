# test_email_only.py
import sys
import os
import logging
from services.notification_services import NotificationService

logging.basicConfig(level=logging.INFO)

def test_email():
    """Test email notification"""
    
    notifier = NotificationService()
    
    print("\n" + "="*60)
    print("📧 AGRIGUARD EMAIL TEST")
    print("="*60)
    
    email = input("Enter email address: ")
    
    if email:
        print("\n📤 Sending test email...")
        result = notifier.send_email_alert(
            email=email,
            animal_tag="TEST123",
            anomaly_type="Test Alert",
            location="Test Location",
            severity="Low",
            details="This is a test email from AgriGuard system."
        )
        
        print("\n" + "="*60)
        if result:
            print("✅ EMAIL SENT SUCCESSFULLY!")
            print(f"📧 Check your inbox: {email}")
            print("💡 Check spam folder if not received")
        else:
            print("❌ EMAIL FAILED")
            print("Check logs for error details")
        print("="*60)

if __name__ == "__main__":
    test_email()