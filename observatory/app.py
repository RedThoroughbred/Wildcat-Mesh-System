"""
Wildcat Mesh Network Observatory
Flask Dashboard for Northern Kentucky Mesh
"""
from flask import Flask, render_template, jsonify, Response, request
from flask_socketio import SocketIO, emit
from datetime import datetime
import logging
import threading
import time
import configparser
import os
import shutil
import subprocess

import config
from modules.db import (
    initialize_observatory_tables,
    get_db_connection,
    get_active_nodes,
    get_recent_messages,
    get_mesh_stats,
    get_channel_activity,
    get_low_battery_nodes,
    get_all_nodes_detailed,
    get_node_detail,
    get_hourly_snr_trends,
    get_best_worst_propagation,
    get_snr_distribution,
    get_top_senders,
    get_channel_hourly_activity,
    get_channel_details,
    get_channel_messages,
    get_node_positions,
    get_bbs_messages,
    get_neighbor_info
)

app = Flask(__name__)
app.config.from_object(config)
socketio = SocketIO(app, cors_allowed_origins="*")

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Initialize database tables
initialize_observatory_tables()


# Template filters
@app.template_filter('format_time')
def format_time(timestamp):
    """Format Unix timestamp to readable time"""
    try:
        dt = datetime.fromtimestamp(int(timestamp))
        now = datetime.now()
        diff = (now - dt).total_seconds()

        if diff < 60:
            return "just now"
        elif diff < 3600:
            return f"{int(diff/60)}m ago"
        elif diff < 86400:
            return f"{int(diff/3600)}h ago"
        else:
            return dt.strftime("%b %d, %I:%M %p")
    except:
        return "unknown"


@app.template_filter('channel_name')
def channel_name(channel_index):
    """Get friendly name for channel index"""
    channel_names = {
        0: "LongFast (Primary)",
        1: "Channel 1",
        2: "Channel 2",
        3: "Channel 3",
        4: "Channel 4",
        5: "Channel 5",
        6: "Channel 6",
        7: "Channel 7"
    }
    return channel_names.get(channel_index, f"Channel {channel_index}")


@app.template_filter('timestamp_to_datetime')
def timestamp_to_datetime(timestamp):
    """Format Unix timestamp to readable date time"""
    try:
        dt = datetime.fromtimestamp(int(timestamp))
        return dt.strftime("%b %d, %I:%M:%S %p")
    except:
        return "unknown"


# Routes
@app.route('/')
def dashboard():
    """Main dashboard"""
    stats = get_mesh_stats()
    recent_messages = get_recent_messages(limit=20)
    active_nodes = get_active_nodes(threshold=3600)  # 1 hour
    channel_activity = get_channel_activity()
    low_battery_nodes = get_low_battery_nodes()

    return render_template(
        'dashboard.html',
        stats=stats,
        recent_messages=recent_messages,
        active_nodes=active_nodes,
        channel_activity=channel_activity,
        low_battery_nodes=low_battery_nodes
    )


@app.route('/map')
def map_view():
    """Live map view"""
    return render_template('map.html')


@app.route('/nodes')
def nodes_view():
    """Node list"""
    import time
    stats = get_mesh_stats()
    nodes = get_all_nodes_detailed()
    current_time = int(time.time())
    return render_template('nodes.html', stats=stats, nodes=nodes, current_time=current_time)


@app.route('/node/<node_id>')
def node_detail_view(node_id):
    """Individual node detail page"""
    node_data = get_node_detail(node_id)
    return render_template('node_detail.html', node=node_data)


@app.route('/propagation')
def propagation_view():
    """Propagation analysis"""
    hourly_trends = get_hourly_snr_trends(days=7)
    best_worst = get_best_worst_propagation()
    snr_dist = get_snr_distribution()
    return render_template('propagation.html',
                          hourly_trends=hourly_trends,
                          best_worst=best_worst,
                          snr_distribution=snr_dist)


@app.route('/topology')
def topology_view():
    """Network topology visualization"""
    return render_template('topology.html')


@app.route('/channels')
def channels_view():
    """Channel activity"""
    # Get time range parameters
    senders_hours = request.args.get('senders_hours', 24, type=int)
    heatmap_hours = request.args.get('heatmap_hours', 24, type=int)

    stats = get_mesh_stats()
    channel_activity = get_channel_activity()
    top_senders = get_top_senders(10, hours=senders_hours)
    hourly_activity = get_channel_hourly_activity(hours=heatmap_hours)
    channel_details = get_channel_details()
    return render_template('channels.html',
                         stats=stats,
                         channel_activity=channel_activity,
                         top_senders=top_senders,
                         hourly_activity=hourly_activity,
                         channel_details=channel_details)


@app.route('/channel/<int:channel_id>')
def channel_detail_view(channel_id):
    """Individual channel chat log"""
    # Get time range from query param (default 24 hours)
    hours = request.args.get('hours', 24, type=int)

    messages = get_channel_messages(channel_id, limit=500, hours=hours)
    channel_stats = get_channel_details()
    channel_info = next((ch for ch in channel_stats if ch['channel_index'] == channel_id), None)

    return render_template('channel_detail.html',
                         channel_id=channel_id,
                         channel_info=channel_info,
                         messages=messages,
                         selected_hours=hours)


@app.route('/bbs-messages')
def bbs_messages_view():
    """BBS direct messages (not channel broadcasts)"""
    # Get time range from query param (default 7 days)
    hours = request.args.get('hours', 168, type=int)

    messages = get_bbs_messages(limit=500, hours=hours)
    stats = get_mesh_stats()

    return render_template('bbs_messages.html',
                         messages=messages,
                         stats=stats,
                         selected_hours=hours)


@app.route('/admin')
def admin_view():
    """Admin tools"""
    return render_template('admin.html')


@app.route('/admin/logs')
def admin_logs():
    """Get mesh network activity logs from database"""
    import time

    log_type = request.args.get('type', 'messages')
    limit = request.args.get('limit', 100, type=int)

    conn = get_db_connection()
    c = conn.cursor()

    try:
        logs = []

        if log_type == 'messages':
            # Get recent mesh messages
            c.execute("""
                SELECT timestamp, sender_short_name, message, snr, rssi, channel_index, to_id
                FROM message_logs
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,))

            for row in c.fetchall():
                msg_type = 'broadcast' if row['to_id'] == 4294967295 else 'direct'
                logs.append({
                    'timestamp': row['timestamp'],
                    'type': 'MESSAGE',
                    'source': row['sender_short_name'] or 'Unknown',
                    'details': f"[Ch {row['channel_index']}] [{msg_type}] {row['message'][:80]}",
                    'signal': f"SNR: {row['snr']:.1f}dB" if row['snr'] else ''
                })

        elif log_type == 'telemetry':
            # Get recent telemetry logs
            c.execute("""
                SELECT timestamp, node_name, battery_level, voltage, temperature, channel_util
                FROM telemetry_logs
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,))

            for row in c.fetchall():
                details = []
                if row['battery_level']: details.append(f"Bat: {row['battery_level']}%")
                if row['voltage']: details.append(f"V: {row['voltage']:.2f}V")
                if row['temperature']: details.append(f"Temp: {row['temperature']:.1f}°C")
                if row['channel_util']: details.append(f"ChUtil: {row['channel_util']:.1f}%")

                logs.append({
                    'timestamp': row['timestamp'],
                    'type': 'TELEMETRY',
                    'source': row['node_name'] or 'Unknown',
                    'details': ', '.join(details) if details else 'No data',
                    'signal': ''
                })

        elif log_type == 'position':
            # Get recent position logs
            c.execute("""
                SELECT timestamp, node_name, latitude, longitude, altitude, satellites_in_view
                FROM position_logs
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,))

            for row in c.fetchall():
                logs.append({
                    'timestamp': row['timestamp'],
                    'type': 'POSITION',
                    'source': row['node_name'] or 'Unknown',
                    'details': f"Lat: {row['latitude']:.5f}, Lon: {row['longitude']:.5f}, Alt: {row['altitude']}m, Sats: {row['satellites_in_view'] or 0}",
                    'signal': ''
                })

        elif log_type == 'all':
            # Combine all types (simplified)
            c.execute("""
                SELECT timestamp, sender_short_name as node, 'MESSAGE' as type,
                       substr(message, 1, 60) as details
                FROM message_logs
                UNION ALL
                SELECT timestamp, node_name as node, 'TELEMETRY' as type,
                       'Battery: ' || battery_level || '%' as details
                FROM telemetry_logs
                WHERE battery_level IS NOT NULL
                UNION ALL
                SELECT timestamp, node_name as node, 'POSITION' as type,
                       'GPS: ' || latitude || ',' || longitude as details
                FROM position_logs
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,))

            for row in c.fetchall():
                logs.append({
                    'timestamp': row['timestamp'],
                    'type': row['type'],
                    'source': row['node'] or 'Unknown',
                    'details': row['details'] or '',
                    'signal': ''
                })

        conn.close()

        # Format timestamps
        for log in logs:
            if log['timestamp']:
                try:
                    dt = datetime.fromtimestamp(log['timestamp'])
                    log['timestamp'] = dt.strftime('%H:%M:%S')
                    log['datetime'] = dt.strftime('%b %d %H:%M:%S')
                except:
                    log['timestamp'] = 'unknown'
                    log['datetime'] = 'unknown'

        # Reverse to show oldest first (most recent at bottom for auto-scroll)
        logs.reverse()

        return jsonify({'logs': logs})

    except Exception as e:
        logging.error(f"Error fetching mesh logs: {e}")
        conn.close()
        return jsonify({'logs': [], 'error': str(e)})


@app.route('/admin/bbs-config')
def bbs_config_view():
    """BBS Configuration Editor"""
    bbs_config_path = '/home/seth/Wildcat-TC2-BBS/config.ini'

    # Default config structure
    default_config = {
        'interface': {
            'type': 'serial',
            'hostname': ''
        },
        'sync': {
            'bbs_nodes': ''
        },
        'menu': {
            'main_menu_items': 'W, N, R, Q, G, B, U, X',
            'bbs_menu_items': 'M, B, C, J, X',
            'utilities_menu_items': 'S, L, X'
        }
    }

    # Read current config
    parser = configparser.ConfigParser()

    try:
        if not os.path.exists(bbs_config_path):
            # Config file doesn't exist - use defaults
            config_data = default_config.copy()
            logging.warning(f"BBS config file not found at {bbs_config_path}, using defaults")
        else:
            parser.read(bbs_config_path)
            # Convert to dict for template
            config_data = {section: dict(parser.items(section)) for section in parser.sections()}

            # Ensure all required sections exist with defaults
            for section, defaults in default_config.items():
                if section not in config_data:
                    config_data[section] = defaults
                else:
                    # Ensure all keys exist within the section
                    for key, default_value in defaults.items():
                        if key not in config_data[section]:
                            config_data[section][key] = default_value

    except Exception as e:
        logging.error(f"Error reading BBS config: {e}")
        # Provide defaults on error
        config_data = default_config.copy()

    return render_template('bbs_config.html', config=config_data, config_path=bbs_config_path)


@app.route('/admin/bbs-config/save', methods=['POST'])
def save_bbs_config():
    """Save BBS configuration"""
    try:
        bbs_config_path = '/home/seth/Wildcat-TC2-BBS/config.ini'

        # Create backup
        backup_path = f"{bbs_config_path}.backup"
        shutil.copy2(bbs_config_path, backup_path)

        # Get form data
        config_data = request.json

        # Write new config
        parser = configparser.ConfigParser()
        for section, values in config_data.items():
            parser.add_section(section)
            for key, value in values.items():
                parser.set(section, key, value)

        with open(bbs_config_path, 'w') as f:
            parser.write(f)

        return jsonify({
            'success': True,
            'message': 'Configuration saved successfully!',
            'backup_created': backup_path
        })

    except Exception as e:
        logging.error(f"Error saving BBS config: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/admin/bbs-config/restart', methods=['POST'])
def restart_bbs_service():
    """Restart BBS service"""
    try:
        result = subprocess.run(
            ['sudo', 'systemctl', 'restart', 'mesh-bbs.service'],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            return jsonify({
                'success': True,
                'message': 'BBS service restarted successfully!'
            })
        else:
            return jsonify({
                'success': False,
                'message': f'Error: {result.stderr}'
            }), 500

    except Exception as e:
        logging.error(f"Error restarting BBS: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/admin/bbs-content')
def bbs_content_view():
    """BBS Content Editor (fortunes, trivia, resources)"""
    bbs_path = '/home/seth/Wildcat-TC2-BBS'

    # Read content files
    content_files = {}

    try:
        with open(f'{bbs_path}/fortunes.txt', 'r') as f:
            content_files['fortunes'] = f.read()
    except:
        content_files['fortunes'] = ""

    try:
        with open(f'{bbs_path}/trivia.txt', 'r') as f:
            content_files['trivia'] = f.read()
    except:
        content_files['trivia'] = ""

    return render_template('bbs_content.html', content=content_files, bbs_path=bbs_path)


@app.route('/admin/bbs-content/save', methods=['POST'])
def save_bbs_content():
    """Save BBS content files"""
    try:
        bbs_path = '/home/seth/Wildcat-TC2-BBS'
        data = request.json
        file_type = data.get('file_type')
        content = data.get('content')

        file_map = {
            'fortunes': f'{bbs_path}/fortunes.txt',
            'trivia': f'{bbs_path}/trivia.txt'
        }

        if file_type not in file_map:
            return jsonify({'success': False, 'message': 'Invalid file type'}), 400

        file_path = file_map[file_type]

        # Create backup
        backup_path = f"{file_path}.backup"
        if os.path.exists(file_path):
            shutil.copy2(file_path, backup_path)

        # Write new content
        with open(file_path, 'w') as f:
            f.write(content)

        return jsonify({
            'success': True,
            'message': f'{file_type.title()} saved successfully!',
            'backup_created': backup_path
        })

    except Exception as e:
        logging.error(f"Error saving BBS content: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api')
def api_docs_view():
    """API documentation"""
    return render_template('api_docs.html')


# API Endpoints
@app.route('/api/v1/stats')
def api_stats():
    """Get mesh statistics (JSON)"""
    return jsonify(get_mesh_stats())


@app.route('/api/v1/nodes')
def api_nodes():
    """Get active nodes (JSON)"""
    return jsonify(get_active_nodes())


@app.route('/api/v1/messages')
def api_messages():
    """Get recent messages (JSON)"""
    return jsonify(get_recent_messages())


@app.route('/api/v1/positions')
def api_positions():
    """Get node positions (JSON)"""
    return jsonify(get_node_positions())


@app.route('/api/v1/top-senders')
def api_top_senders():
    """Get top senders (JSON)"""
    hours = request.args.get('hours', 24, type=int)
    return jsonify(get_top_senders(10, hours=hours))


@app.route('/api/v1/hourly-activity')
def api_hourly_activity():
    """Get hourly activity (JSON)"""
    hours = request.args.get('hours', 24, type=int)
    return jsonify(get_channel_hourly_activity(hours=hours))


@app.route('/api/v1/neighbor-info')
def api_neighbor_info():
    """Get network topology neighbor information (JSON)"""
    return jsonify(get_neighbor_info())


# Export endpoints
@app.route('/export/nodes.csv')
def export_nodes_csv():
    """Export nodes list as CSV"""
    import csv
    import io

    nodes = get_all_nodes_detailed()

    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow(['Node ID', 'Short Name', 'Messages', 'Avg SNR (dB)', 'Best SNR (dB)',
                    'Worst SNR (dB)', 'Avg RSSI (dBm)', 'First Seen', 'Last Seen'])

    # Data
    for node in nodes:
        writer.writerow([
            node['sender_id'],
            node['sender_short_name'],
            node['message_count'],
            round(node['avg_snr'], 2) if node['avg_snr'] else '',
            round(node['best_snr'], 2) if node['best_snr'] else '',
            round(node['worst_snr'], 2) if node['worst_snr'] else '',
            round(node['avg_rssi'], 0) if node['avg_rssi'] else '',
            datetime.fromtimestamp(node['first_seen']).isoformat() if node['first_seen'] else '',
            datetime.fromtimestamp(node['last_seen']).isoformat() if node['last_seen'] else ''
        ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=mesh_nodes.csv'}
    )


@app.route('/export/messages.csv')
def export_messages_csv():
    """Export message history as CSV"""
    import csv
    import io

    messages = get_recent_messages(limit=1000)  # Export last 1000

    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow(['Timestamp', 'Node', 'Message', 'SNR (dB)', 'RSSI (dBm)', 'Channel'])

    # Data
    for msg in messages:
        writer.writerow([
            datetime.fromtimestamp(msg['timestamp']).isoformat() if msg['timestamp'] else '',
            msg['sender_short_name'],
            msg['message'],
            round(msg['snr'], 2) if msg['snr'] else '',
            msg['rssi'] if msg['rssi'] else '',
            msg['channel_index'] if msg['channel_index'] is not None else ''
        ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=mesh_messages.csv'}
    )


# WebSocket event handlers
@socketio.on('connect')
def handle_connect():
    """Client connected"""
    logging.info('Client connected to WebSocket')
    emit('connected', {'status': 'connected'})


@socketio.on('disconnect')
def handle_disconnect():
    """Client disconnected"""
    logging.info('Client disconnected from WebSocket')


# Background thread to push updates
def background_stats_updater():
    """Push stats updates to all connected clients every 5 seconds"""
    while True:
        time.sleep(5)
        try:
            stats = get_mesh_stats()
            active_nodes = get_active_nodes(threshold=3600)
            recent_messages = get_recent_messages(limit=5)

            socketio.emit('stats_update', {
                'stats': stats,
                'active_node_count': len(active_nodes),
                'latest_message': recent_messages[0] if recent_messages else None
            })
        except Exception as e:
            logging.error(f"Error in background updater: {e}")


# Start background thread
thread = threading.Thread(target=background_stats_updater, daemon=True)
thread.start()


if __name__ == '__main__':
    print(f"""
    ╔══════════════════════════════════════════╗
    ║  🛰️  Wildcat Mesh Observatory           ║
    ║                                          ║
    ║  Dashboard: http://{config.HOST}:{config.PORT}    ║
    ║  Status: READY (WebSocket Enabled)       ║
    ╚══════════════════════════════════════════╝
    """)

    socketio.run(
        app,
        host=config.HOST,
        port=config.PORT,
        debug=config.DEBUG,
        allow_unsafe_werkzeug=True
    )
