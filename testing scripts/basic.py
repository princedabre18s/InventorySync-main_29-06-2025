import os

# Create email templates directory if it doesn't exist
email_templates_dir = os.path.join('templates', 'email_templates')
os.makedirs(email_templates_dir, exist_ok=True)