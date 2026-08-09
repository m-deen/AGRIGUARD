# test_carrier_gateways.py
import os
import sys
import logging
from services.notification_services import NotificationService

logging.basicConfig(level=logging.INFO)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def test_all_carriers():
    """Test all carrier gateways for your phone number"""
    
    notifier = NotificationService()
    
    # Get phone number
    phone = input("Enter your 10-digit phone number (no dashes, e.g., 1234567890): ")
    
    # List of US carriers
    carriers = ['att', 'verizon', 'tmobile', 'sprint', 'cricket', 'googlefi', 'uscellular', 'metropcs']
    
    print("\n🔍 Testing all carrier gateways...")
    print("=" * 50)
    
    results = []
    
    for carrier in carriers:
        print(f"\n📱 Testing {carrier.upper()}...")
        
        success = notifier.send_sms_alert(
            phone_number=phone,
            animal_tag="TEST123",
            carrier=carrier,
            anomaly_type="Carrier Test",
            location=f"Testing {carrier}"
        )
        
        status = "✅ SENT" if success else "❌ FAILED"
        print(f"  {carrier}: {status}")
        results.append((carrier, success))
    
    print("\n" + "=" * 50)
    print("📊 Results Summary:")
    
    successful = [c for c, s in results if s]
    failed = [c for c, s in results if not s]
    
    if successful:
        print(f"\n✅ Working carriers: {', '.join(successful)}")
    if failed:
        print(f"❌ Failed carriers: {', '.join(failed)}")
    
    print("\n💡 If all failed, your carrier might not support email-to-SMS.")
    print("Try using a different method below.")

if __name__ == "__main__":
    test_all_carriers()