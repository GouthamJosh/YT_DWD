"""
Configuration and Admin/Database Management
"""
import os
import logging
from typing import List, Optional
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

logger = logging.getLogger("YTBot")

# ═══════════════════════════════════════════
#          ADMIN CONFIGURATION
# ═══════════════════════════════════════════

# Admin IDs from environment (comma-separated)
# Example: ADMIN_IDS="123456789,987654321"
ADMIN_IDS = []
admin_str = os.environ.get("ADMIN_IDS", "").strip()
if admin_str:
    try:
        ADMIN_IDS = [int(x.strip()) for x in admin_str.split(",") if x.strip()]
    except ValueError:
        logger.warning("Invalid ADMIN_IDS format. Expected comma-separated integers.")

logger.info(f"Admins configured: {len(ADMIN_IDS)} admin(s)")

# ═══════════════════════════════════════════
#          MONGODB CONFIGURATION
# ═══════════════════════════════════════════

MONGODB_URI = os.environ.get("MONGODB_URI", "")
DB_NAME = os.environ.get("DB_NAME", "yt_downloader")
COOKIES_COLLECTION = "cookies"
ADMIN_LOGS_COLLECTION = "admin_logs"

mongo_client: Optional[AsyncIOMotorClient] = None
db: Optional[AsyncIOMotorDatabase] = None

async def init_mongodb():
    """Initialize MongoDB connection"""
    global mongo_client, db
    
    if not MONGODB_URI:
        logger.warning("MONGODB_URI not set. Database features will be disabled.")
        return False
    
    try:
        mongo_client = AsyncIOMotorClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
        # Test connection
        await mongo_client.admin.command('ping')
        db = mongo_client[DB_NAME]
        logger.info(f"✅ MongoDB connected: {DB_NAME}")
        
        # Create indexes
        await db[COOKIES_COLLECTION].create_index("timestamp")
        await db[ADMIN_LOGS_COLLECTION].create_index("timestamp")
        
        return True
    except Exception as e:
        logger.error(f"❌ MongoDB connection failed: {e}")
        mongo_client = None
        db = None
        return False

async def close_mongodb():
    """Close MongoDB connection"""
    global mongo_client
    if mongo_client:
        mongo_client.close()
        logger.info("MongoDB connection closed")

# ═══════════════════════════════════════════
#          ADMIN VERIFICATION
# ═══════════════════════════════════════════

def is_admin(user_id: int) -> bool:
    """Check if user is an admin"""
    if not ADMIN_IDS:  # No admins configured = anyone is admin
        return True
    return user_id in ADMIN_IDS

def is_auth_user(user_id: int, auth_users: List[int]) -> bool:
    """Check if user is authorized"""
    return not auth_users or user_id in auth_users

# ═══════════════════════════════════════════
#          DATABASE OPERATIONS
# ═══════════════════════════════════════════

async def save_cookies_to_db(file_path: str, admin_id: int, notes: str = ""):
    """Save cookies.txt info to MongoDB"""
    if not db:
        logger.warning("Database not initialized, skipping save")
        return False
    
    try:
        import time
        from datetime import datetime
        
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        
        doc = {
            "timestamp": datetime.utcnow(),
            "admin_id": admin_id,
            "file_size": file_size,
            "file_path": file_path,
            "notes": notes,
            "status": "active"
        }
        
        result = await db[COOKIES_COLLECTION].insert_one(doc)
        logger.info(f"✅ Cookies saved to DB: {result.inserted_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to save cookies to DB: {e}")
        return False

async def log_admin_action(admin_id: int, action: str, details: str = ""):
    """Log admin actions to MongoDB"""
    if not db:
        return False
    
    try:
        from datetime import datetime
        
        doc = {
            "timestamp": datetime.utcnow(),
            "admin_id": admin_id,
            "action": action,
            "details": details
        }
        
        await db[ADMIN_LOGS_COLLECTION].insert_one(doc)
        return True
    except Exception as e:
        logger.error(f"❌ Failed to log admin action: {e}")
        return False

async def get_latest_cookies_info():
    """Get latest cookies.txt info from database"""
    if not db:
        return None
    
    try:
        doc = await db[COOKIES_COLLECTION].find_one(
            {"status": "active"},
            sort=[("timestamp", -1)]
        )
        return doc
    except Exception as e:
        logger.error(f"❌ Failed to fetch cookies info: {e}")
        return None

async def get_admin_logs(limit: int = 10):
    """Get recent admin logs"""
    if not db:
        return []
    
    try:
        logs = await db[ADMIN_LOGS_COLLECTION].find().sort("timestamp", -1).limit(limit).to_list(length=limit)
        return logs
    except Exception as e:
        logger.error(f"❌ Failed to fetch admin logs: {e}")
        return []
