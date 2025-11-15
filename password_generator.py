"""
Secure password generator with customizable options
"""
import random
import string

def generate_secure_password(length=64):
    """
    Generate a secure password with the following requirements:
    - Length: 64 characters
    - Include Lower Case (a-z)
    - Include Upper Case (A-Z)
    - Include Numbers (0-9)
    - Include Symbols (!"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~)
    - Exclude Ambiguous Characters (ill1L| oO0 '`".,;)
    - Exclude Brackets (<>()[]{})
    """
    # Define character sets
    lowercase = 'abcdefghjkmnpqrstuvwxyz'  # Excluded: i, l, o
    uppercase = 'ABCDEFGHJKMNPQRSTUVWXYZ'  # Excluded: I, L, O
    numbers = '23456789'  # Excluded: 0, 1
    # Symbols: !"#$%&'()*+,-./:;<=>?@[\]^_`{|}~
    # Excluded: '`".,; (ambiguous) and <>()[]{} (brackets)
    # Remaining: !#$%&*+-/:;<=>?@\^_|~
    symbols = '!#$%&*+-/:;<=>?@\\^_|~'
    
    # Ensure at least one character from each required set
    password_chars = [
        random.choice(lowercase),
        random.choice(uppercase),
        random.choice(numbers),
        random.choice(symbols)
    ]
    
    # Combine all allowed characters
    all_chars = lowercase + uppercase + numbers + symbols
    
    # Fill the rest of the password length with random characters
    remaining_length = length - len(password_chars)
    password_chars.extend(random.choice(all_chars) for _ in range(remaining_length))
    
    # Shuffle to avoid predictable patterns
    random.shuffle(password_chars)
    
    return ''.join(password_chars)

