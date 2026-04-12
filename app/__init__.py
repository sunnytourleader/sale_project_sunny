import os
import logging
from logging.handlers import RotatingFileHandler
from flask import Flask

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'your_super_secret_dev_key_12345'
    app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
    
    # Ensure upload folder exists
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    # 1. Standard Server Logging
    if not os.path.exists('logs'): os.mkdir('logs')
    file_handler = RotatingFileHandler('logs/saledash.log', maxBytes=102400, backupCount=10)
    file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'))
    file_handler.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)
    app.logger.info('Sale Dashboard startup initialized.')

    # 2. Dedicated User Activity Logger
    activity_logger = logging.getLogger('activity_tracker')
    activity_logger.setLevel(logging.INFO)
    activity_handler = RotatingFileHandler('logs/activities.log', maxBytes=500000, backupCount=10)
    activity_handler.setFormatter(logging.Formatter('%(asctime)s | %(message)s'))
    activity_logger.addHandler(activity_handler)

    # 3. Initialize Core Setup & Blueprints (Safely avoids circular imports)
    with app.app_context():
        from .core import login_manager, init_db_tables, inject_permissions, log_page_views
        
        login_manager.init_app(app)
        init_db_tables()
        
        app.context_processor(inject_permissions)
        app.before_request(log_page_views)

        # Import the modular feature routes
        from .bp_auth import auth_bp
        from .bp_dashboard import dashboard_bp
        from .bp_tools import tools_bp
        from .bp_admin import admin_bp

        # Register them into the main app
        app.register_blueprint(auth_bp)
        app.register_blueprint(dashboard_bp)
        app.register_blueprint(tools_bp)
        app.register_blueprint(admin_bp)

    return app