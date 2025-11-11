import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime

from meshtastic import BROADCAST_NUM

from utils import (
    send_bulletin_to_bbs_nodes,
    send_delete_bulletin_to_bbs_nodes,
    send_delete_mail_to_bbs_nodes,
    send_mail_to_bbs_nodes, send_message, send_channel_to_bbs_nodes
)


thread_local = threading.local()

def get_db_connection():
    # Database path - shared with Observatory
    db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'shared', 'bulletins.db')
    if not hasattr(thread_local, 'connection'):
        thread_local.connection = sqlite3.connect(db_path)
    return thread_local.connection

def initialize_database():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS bulletins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    board TEXT NOT NULL,
                    sender_short_name TEXT NOT NULL,
                    date TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    content TEXT NOT NULL,
                    unique_id TEXT NOT NULL
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS mail (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender TEXT NOT NULL,
                    sender_short_name TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    date TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    content TEXT NOT NULL,
                    unique_id TEXT NOT NULL
                );''')
    c.execute('''CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL
                );''')
    c.execute('''CREATE TABLE IF NOT EXISTS message_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    sender_id TEXT NOT NULL,
                    sender_short_name TEXT NOT NULL,
                    to_id INTEGER NOT NULL,
                    channel_index INTEGER,
                    message TEXT NOT NULL,
                    snr REAL,
                    rssi INTEGER,
                    hop_limit INTEGER
                );''')
    conn.commit()
    print("Database schema initialized.")

def add_channel(name, url, bbs_nodes=None, interface=None):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT INTO channels (name, url) VALUES (?, ?)", (name, url))
    conn.commit()

    if bbs_nodes and interface:
        send_channel_to_bbs_nodes(name, url, bbs_nodes, interface)


def get_channels():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT name, url FROM channels")
    return c.fetchall()



def add_bulletin(board, sender_short_name, subject, content, bbs_nodes, interface, unique_id=None):
    conn = get_db_connection()
    c = conn.cursor()
    date = datetime.now().strftime('%Y-%m-%d %H:%M')
    if not unique_id:
        unique_id = str(uuid.uuid4())
    c.execute(
        "INSERT INTO bulletins (board, sender_short_name, date, subject, content, unique_id) VALUES (?, ?, ?, ?, ?, ?)",
        (board, sender_short_name, date, subject, content, unique_id))
    conn.commit()
    if bbs_nodes and interface:
        send_bulletin_to_bbs_nodes(board, sender_short_name, subject, content, unique_id, bbs_nodes, interface)

    # New logic to send group chat notification for urgent bulletins
    if board.lower() == "urgent":
        notification_message = f"💥NEW URGENT BULLETIN💥\nFrom: {sender_short_name}\nTitle: {subject}\nDM 'CB,,Urgent' to view"
        send_message(notification_message, BROADCAST_NUM, interface)

    return unique_id


def get_bulletins(board):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, subject, sender_short_name, date, unique_id FROM bulletins WHERE board = ? COLLATE NOCASE", (board,))
    return c.fetchall()

def get_bulletin_content(bulletin_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT sender_short_name, date, subject, content, unique_id FROM bulletins WHERE id = ?", (bulletin_id,))
    return c.fetchone()


def delete_bulletin(bulletin_id, bbs_nodes, interface):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM bulletins WHERE id = ?", (bulletin_id,))
    conn.commit()
    send_delete_bulletin_to_bbs_nodes(bulletin_id, bbs_nodes, interface)

def add_mail(sender_id, sender_short_name, recipient_id, subject, content, bbs_nodes, interface, unique_id=None):
    conn = get_db_connection()
    c = conn.cursor()
    date = datetime.now().strftime('%Y-%m-%d %H:%M')
    if not unique_id:
        unique_id = str(uuid.uuid4())
    c.execute("INSERT INTO mail (sender, sender_short_name, recipient, date, subject, content, unique_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (sender_id, sender_short_name, recipient_id, date, subject, content, unique_id))
    conn.commit()
    if bbs_nodes and interface:
        send_mail_to_bbs_nodes(sender_id, sender_short_name, recipient_id, subject, content, unique_id, bbs_nodes, interface)
    return unique_id

def get_mail(recipient_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, sender_short_name, subject, date, unique_id FROM mail WHERE recipient = ?", (recipient_id,))
    return c.fetchall()

def get_mail_content(mail_id, recipient_id):
    # TODO: ensure only recipient can read mail
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT sender_short_name, date, subject, content, unique_id FROM mail WHERE id = ? and recipient = ?", (mail_id, recipient_id,))
    return c.fetchone()

def delete_mail(unique_id, recipient_id, bbs_nodes, interface):
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("SELECT recipient FROM mail WHERE unique_id = ?", (unique_id,))
        result = c.fetchone()
        if result is None:
            logging.error(f"No mail found with unique_id: {unique_id}")
            return  # Early exit if no matching mail found
        recipient_id = result[0]
        logging.info(f"Attempting to delete mail with unique_id: {unique_id} by {recipient_id}")
        c.execute("DELETE FROM mail WHERE unique_id = ? and recipient = ?", (unique_id, recipient_id,))
        conn.commit()
        send_delete_mail_to_bbs_nodes(unique_id, bbs_nodes, interface)
        logging.info(f"Mail with unique_id: {unique_id} deleted and sync message sent.")
    except Exception as e:
        logging.error(f"Error deleting mail with unique_id {unique_id}: {e}")
        raise


def get_sender_id_by_mail_id(mail_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT sender FROM mail WHERE id = ?", (mail_id,))
    result = c.fetchone()
    if result:
        return result[0]
    return None


def log_message(sender_id, sender_short_name, to_id, message, timestamp, channel_index=0, snr=None, rssi=None, hop_limit=None):
    """Log a message to the database for analytics"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "INSERT INTO message_logs (timestamp, sender_id, sender_short_name, to_id, channel_index, message, snr, rssi, hop_limit) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (timestamp, sender_id, sender_short_name, to_id, channel_index, message, snr, rssi, hop_limit))
        conn.commit()
    except Exception as e:
        logging.error(f"Error logging message: {e}")


def get_channel_activity_stats(hours=24):
    """Get message count by channel for the last N hours"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        import time
        cutoff_time = int(time.time()) - (hours * 3600)
        
        c.execute("""
            SELECT channel_index, COUNT(*) as count 
            FROM message_logs 
            WHERE timestamp >= ? 
            GROUP BY channel_index 
            ORDER BY count DESC
        """, (cutoff_time,))
        
        return c.fetchall()
    except Exception as e:
        logging.error(f"Error getting channel activity: {e}")
        return []


def get_message_stats(hours=24):
    """Get general message statistics"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        import time
        cutoff_time = int(time.time()) - (hours * 3600)
        
        # Total messages
        c.execute("SELECT COUNT(*) FROM message_logs WHERE timestamp >= ?", (cutoff_time,))
        total = c.fetchone()[0]
        
        # Messages by sender
        c.execute("""
            SELECT sender_short_name, COUNT(*) as count 
            FROM message_logs 
            WHERE timestamp >= ? 
            GROUP BY sender_short_name 
            ORDER BY count DESC 
            LIMIT 10
        """, (cutoff_time,))
        top_senders = c.fetchall()
        
        # Average SNR
        c.execute("SELECT AVG(snr) FROM message_logs WHERE timestamp >= ? AND snr IS NOT NULL", (cutoff_time,))
        avg_snr = c.fetchone()[0]
        
        return {
            'total': total,
            'top_senders': top_senders,
            'avg_snr': avg_snr if avg_snr else 0
        }
    except Exception as e:
        logging.error(f"Error getting message stats: {e}")
        return {'total': 0, 'top_senders': [], 'avg_snr': 0}


# ========== PROPAGATION STUDY TOOLS ==========

def get_propagation_trends(hours=24, node_id=None):
    """Get SNR/RSSI trends over time"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        import time
        cutoff_time = int(time.time()) - (hours * 3600)
        
        if node_id:
            # Specific node analysis
            c.execute("""
                SELECT timestamp, snr, rssi, hop_limit 
                FROM message_logs 
                WHERE timestamp >= ? AND sender_id = ? AND snr IS NOT NULL
                ORDER BY timestamp ASC
            """, (cutoff_time, node_id))
        else:
            # All nodes average
            c.execute("""
                SELECT timestamp, AVG(snr) as avg_snr, AVG(rssi) as avg_rssi, COUNT(*) as msg_count
                FROM message_logs 
                WHERE timestamp >= ? AND snr IS NOT NULL
                GROUP BY timestamp / 3600
                ORDER BY timestamp ASC
            """, (cutoff_time,))
        
        return c.fetchall()
    except Exception as e:
        logging.error(f"Error getting propagation trends: {e}")
        return []


def get_best_worst_conditions():
    """Find best and worst propagation periods"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # Best SNR in last 7 days
        c.execute("""
            SELECT sender_short_name, MAX(snr) as best_snr, timestamp
            FROM message_logs 
            WHERE timestamp >= ? AND snr IS NOT NULL
            GROUP BY sender_id
            ORDER BY best_snr DESC
            LIMIT 10
        """, (int(__import__('time').time()) - 604800,))
        best = c.fetchall()
        
        # Worst SNR
        c.execute("""
            SELECT sender_short_name, MIN(snr) as worst_snr, timestamp
            FROM message_logs 
            WHERE timestamp >= ? AND snr IS NOT NULL
            GROUP BY sender_id
            ORDER BY worst_snr ASC
            LIMIT 10
        """, (int(__import__('time').time()) - 604800,))
        worst = c.fetchall()
        
        return {'best': best, 'worst': worst}
    except Exception as e:
        logging.error(f"Error getting best/worst conditions: {e}")
        return {'best': [], 'worst': []}


def get_hourly_propagation_stats():
    """Get average propagation by hour of day (find best times)"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # Group by hour of day over last 7 days
        c.execute("""
            SELECT 
                strftime('%H', datetime(timestamp, 'unixepoch', 'localtime')) as hour,
                AVG(snr) as avg_snr,
                AVG(rssi) as avg_rssi,
                COUNT(*) as msg_count
            FROM message_logs 
            WHERE timestamp >= ? AND snr IS NOT NULL
            GROUP BY hour
            ORDER BY hour ASC
        """, (int(__import__('time').time()) - 604800,))
        
        return c.fetchall()
    except Exception as e:
        logging.error(f"Error getting hourly stats: {e}")
        return []


def get_node_reliability(node_id):
    """Calculate reliability metrics for a specific node"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # Messages received in last 7 days
        c.execute("""
            SELECT COUNT(*), AVG(snr), MIN(snr), MAX(snr), AVG(rssi)
            FROM message_logs 
            WHERE sender_id = ? AND timestamp >= ?
        """, (node_id, int(__import__('time').time()) - 604800))
        
        stats = c.fetchone()
        return {
            'message_count': stats[0],
            'avg_snr': stats[1] if stats[1] else 0,
            'min_snr': stats[2] if stats[2] else 0,
            'max_snr': stats[3] if stats[3] else 0,
            'avg_rssi': stats[4] if stats[4] else 0
        }
    except Exception as e:
        logging.error(f"Error getting node reliability: {e}")
        return {}
