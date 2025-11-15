"""
Office 365 OAuth authentication module
"""
import msal
from flask import session
from config import (
    OAUTH_CLIENT_ID, 
    OAUTH_CLIENT_SECRET, 
    OAUTH_AUTHORITY, 
    OAUTH_REDIRECT_PATH,
    OAUTH_SCOPES,
    ALLOWED_DOMAINS
)

def _build_msal_app(cache=None, authority=None):
    """Build MSAL application"""
    return msal.ConfidentialClientApplication(
        OAUTH_CLIENT_ID,
        authority=authority or OAUTH_AUTHORITY,
        client_credential=OAUTH_CLIENT_SECRET,
        token_cache=cache
    )

def _build_auth_code_flow(authority=None, scopes=None, redirect_uri=None):
    """Build authorization code flow"""
    return _build_msal_app(authority=authority).initiate_auth_code_flow(
        scopes or OAUTH_SCOPES,
        redirect_uri=redirect_uri
    )

def _get_token_from_cache(scope=None):
    """Get token from cache"""
    cache = _load_cache()
    cca = _build_msal_app(cache=cache)
    accounts = cca.get_accounts()
    if accounts:
        result = cca.acquire_token_silent(scope or OAUTH_SCOPES, account=accounts[0])
        _save_cache(cache)
        return result
    return None

def _load_cache():
    """Load token cache from session"""
    cache = msal.SerializableTokenCache()
    if session.get("token_cache"):
        cache.deserialize(session["token_cache"])
    return cache

def _save_cache(cache):
    """Save token cache to session"""
    if cache.has_state_changed:
        session["token_cache"] = cache.serialize()

def get_user_email():
    """Get user email from session"""
    return session.get("user_email")

def get_user_name():
    """Get user name from session"""
    return session.get("user_name")

def is_domain_allowed(email):
    """Check if email domain is in allowed list"""
    if not email:
        return False
    domain = email.split('@')[-1].lower()
    return domain in [d.lower() for d in ALLOWED_DOMAINS]

def validate_user_domain(email):
    """Validate user email domain"""
    if not is_domain_allowed(email):
        raise ValueError(f"Access denied. Email domain must be one of: {', '.join(ALLOWED_DOMAINS)}")
    return True

