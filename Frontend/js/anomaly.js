// frontend/js/anomaly.js

async function detectAnomaly(animalTag, email, anomalyType, location, severity, details) {
    try {
        const response = await fetch('http://localhost:5000/api/anomaly/detect', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                // If you uncomment @token_required, add this:
                // 'Authorization': `Bearer ${localStorage.getItem('token')}`
            },
            body: JSON.stringify({
                animal_tag: animalTag,
                email: email,
                anomaly_type: anomalyType,
                location: location,
                severity: severity,
                details: details
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            console.log('✅ Alert sent!', data);
            alert(`Alert sent to ${email}!`);
            return data;
        } else {
            console.error('❌ Failed:', data.message);
            alert(`Error: ${data.message}`);
            return null;
        }
    } catch (error) {
        console.error('❌ Network error:', error);
        alert('Network error. Check if server is running.');
        return null;
    }
}

// Example usage - call this when an anomaly is detected
// detectAnomaly('AG12345', 'farmer@example.com', 'Irregular movement', 'Pasture B-3', 'High', 'Animal deviated from normal pattern');