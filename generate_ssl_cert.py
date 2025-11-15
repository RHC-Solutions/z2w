"""
Helper script to generate self-signed SSL certificates for HTTPS testing
Run this script to generate cert.pem and key.pem files for local development
"""
import ssl
import socket
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from datetime import datetime, timedelta
import os

def generate_self_signed_cert():
    """Generate a self-signed SSL certificate"""
    # Generate private key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    
    # Get hostname
    hostname = socket.gethostname()
    
    # Create certificate
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "State"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "City"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Z2W Offloader"),
        x509.NameAttribute(NameOID.COMMON_NAME, hostname),
    ])
    
    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        private_key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.utcnow()
    ).not_valid_after(
        datetime.utcnow() + timedelta(days=365)
    ).add_extension(
        x509.SubjectAlternativeName([
            x509.DNSName(hostname),
            x509.DNSName("localhost"),
            x509.IPAddress(socket.inet_aton("127.0.0.1")),
        ]),
        critical=False,
    ).sign(private_key, hashes.SHA256())
    
    # Write certificate
    cert_path = "cert.pem"
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    
    # Write private key
    key_path = "key.pem"
    with open(key_path, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        ))
    
    print(f"✓ SSL certificate generated successfully!")
    print(f"  Certificate: {os.path.abspath(cert_path)}")
    print(f"  Private Key: {os.path.abspath(key_path)}")
    print(f"\nTo use these certificates, add to your .env file:")
    print(f"  SSL_CERT_PATH={os.path.abspath(cert_path)}")
    print(f"  SSL_KEY_PATH={os.path.abspath(key_path)}")
    print(f"\nNote: This is a self-signed certificate for development only.")
    print(f"      Browsers will show a security warning. For production,")
    print(f"      use certificates from a trusted Certificate Authority (CA).")

if __name__ == "__main__":
    try:
        generate_self_signed_cert()
    except ImportError:
        print("Error: cryptography library is required.")
        print("Install it with: pip install cryptography")
    except Exception as e:
        print(f"Error generating certificate: {e}")

