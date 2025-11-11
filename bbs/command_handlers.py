import configparser
import logging
import random
import time
import requests
import json

from meshtastic import BROADCAST_NUM

from db_operations import (
    add_bulletin, add_mail, delete_mail,
    get_bulletin_content, get_bulletins,
    get_mail, get_mail_content,
    add_channel, get_channels, get_sender_id_by_mail_id
)
from utils import (
    get_node_id_from_num, get_node_info,
    get_node_short_name, send_message,
    update_user_state
)

# Read the configuration for menu options
config = configparser.ConfigParser()
config.read('config.ini')

main_menu_items = config['menu']['main_menu_items'].split(',')
bbs_menu_items = config['menu']['bbs_menu_items'].split(',')
utilities_menu_items = config['menu']['utilities_menu_items'].split(',')

# Load messages from JSON
def load_messages():
    """Load BBS messages from messages.json"""
    try:
        with open('messages.json', 'r') as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Error loading messages.json: {e}")
        return {}

MESSAGES = load_messages()


def build_menu(items, menu_name):
    menu_str = f"{menu_name}\n"
    labels = MESSAGES.get('menu_labels', {})

    for item in items:
        item = item.strip()
        if item == 'W':
            # Context-aware: Weather for main menu, Wall of Shame for utilities
            if "Utilities" in menu_name or "🛠️" in menu_name:
                menu_str += labels.get('wall_of_shame', '[W]all of Shame') + "\n"
            else:
                menu_str += labels.get('weather', '[W]eather') + "\n"
        elif item == 'N':
            menu_str += labels.get('network_info', '[N]etwork Info') + "\n"
        elif item == 'Q':
            if "BBS" in menu_name or "💾" in menu_name:
                menu_str += labels.get('quote', '[Q]uote') + "\n"
            else:
                menu_str += labels.get('quick_commands', '[Q]uick Commands') + "\n"
        elif item == 'R':
            menu_str += labels.get('resources', '[R]esources') + "\n"
        elif item == 'B':
            menu_str += labels.get('bulletins', '[B]ulletins') + "\n"
        elif item == 'U':
            menu_str += labels.get('utilities', '[U]tilities') + "\n"
        elif item == 'X':
            menu_str += labels.get('exit', 'E[X]IT') + "\n"
        elif item == 'M':
            menu_str += labels.get('mail', '[M]ail') + "\n"
        elif item == 'C':
            menu_str += labels.get('channel_dir', '[C]hannel Dir') + "\n"
        elif item == 'J':
            menu_str += labels.get('js8call', '[J]S8CALL') + "\n"
        elif item == 'S':
            menu_str += labels.get('stats', '[S]tats') + "\n"
        elif item == 'F':
            menu_str += labels.get('fortune', '[F]ortune') + "\n"
        elif item == 'G':
            menu_str += labels.get('games', '[G]ames') + "\n"
    return menu_str

def handle_help_command(sender_id, interface, menu_name=None):
    headers = MESSAGES.get('menu_headers', {})

    if menu_name:
        update_user_state(sender_id, {'command': 'MENU', 'menu': menu_name, 'step': 1})
        if menu_name == 'bbs':
            header = headers.get('bbs', '📰BBS Menu📰')
            response = build_menu(bbs_menu_items, header)
        elif menu_name == 'utilities':
            header = headers.get('utilities', '🛠️Utilities Menu🛠️')
            response = build_menu(utilities_menu_items, header)
    else:
        update_user_state(sender_id, {'command': 'MAIN_MENU', 'step': 1})
        mail = get_mail(get_node_id_from_num(sender_id, interface))
        header_template = headers.get('main', '💾Wildcat TC² BBS💾 (✉️:{mail_count})')
        header = header_template.format(mail_count=len(mail))
        response = build_menu(main_menu_items, header)
    send_message(response, sender_id, interface)

def get_node_name(node_id, interface):
    node_info = interface.nodes.get(node_id)
    if node_info:
        return node_info['user']['longName']
    return f"Node {node_id}"


def handle_mail_command(sender_id, interface):
    mail_msgs = MESSAGES.get('mail', {})
    headers = MESSAGES.get('menu_headers', {})
    header = headers.get('mail', '✉️Mail Menu✉️')
    menu = mail_msgs.get('menu', 'What would you like to do with mail?\n[R]ead  [S]end E[X]IT')
    response = f"{header}\n{menu}"
    send_message(response, sender_id, interface)
    update_user_state(sender_id, {'command': 'MAIL', 'step': 1})



def handle_bulletin_command(sender_id, interface):
    bulletin_msgs = MESSAGES.get('bulletins', {})
    headers = MESSAGES.get('menu_headers', {})
    header = headers.get('bulletin', '📰Bulletin Menu📰')
    menu = bulletin_msgs.get('menu', 'Which board would you like to enter?\n[G]eneral  [I]nfo  [N]ews  [U]rgent')
    response = f"{header}\n{menu}"
    send_message(response, sender_id, interface)
    update_user_state(sender_id, {'command': 'BULLETIN_MENU', 'step': 1})


def handle_exit_command(sender_id, interface):
    common = MESSAGES.get('common', {})
    msg = common.get('help_prompt', "Type 'HELP' for a list of commands.")
    send_message(msg, sender_id, interface)
    update_user_state(sender_id, None)


def handle_stats_command(sender_id, interface):
    response = ("📊Stats Menu📊\n"
                "[N]odes  [H]ardware  [R]oles\n"
                "[S]NR Leaders  [D]istance\n"
                "[C]hannel Activity  [T]op Nodes\n"
                "[P]ropagation Analysis\n"
                "E[X]IT")
    send_message(response, sender_id, interface)
    update_user_state(sender_id, {'command': 'STATS', 'step': 1})


def handle_fortune_command(sender_id, interface):
    try:
        with open('fortunes.txt', 'r') as file:
            fortunes = file.readlines()
        if not fortunes:
            send_message("No fortunes available.", sender_id, interface)
            return
        fortune = random.choice(fortunes).strip()
        decorated_fortune = f"🔮 {fortune} 🔮"
        send_message(decorated_fortune, sender_id, interface)
    except Exception as e:
        send_message(f"Error generating fortune: {e}", sender_id, interface)


def handle_stats_steps(sender_id, message, step, interface):
    message = message.lower().strip()
    if len(message) == 2 and message[1] == 'x':
        message = message[0]

    if step == 1:
        choice = message
        if choice == 'x':
            handle_help_command(sender_id, interface)
            return
        elif choice == 'n':
            current_time = int(time.time())
            timeframes = {
                "All time": None,
                "Last 24 hours": 86400,
                "Last 8 hours": 28800,
                "Last hour": 3600
            }
            total_nodes_summary = []

            for period, seconds in timeframes.items():
                if seconds is None:
                    total_nodes = len(interface.nodes)
                else:
                    time_limit = current_time - seconds
                    total_nodes = sum(1 for node in interface.nodes.values() if node.get('lastHeard') is not None and node['lastHeard'] >= time_limit)
                total_nodes_summary.append(f"- {period}: {total_nodes}")

            response = "Total nodes seen:\n" + "\n".join(total_nodes_summary)
            send_message(response, sender_id, interface)
            handle_stats_command(sender_id, interface)
        elif choice == 'h':
            hw_models = {}
            for node in interface.nodes.values():
                hw_model = node['user'].get('hwModel', 'Unknown')
                hw_models[hw_model] = hw_models.get(hw_model, 0) + 1
            response = "Hardware Models:\n" + "\n".join([f"{model}: {count}" for model, count in hw_models.items()])
            send_message(response, sender_id, interface)
            handle_stats_command(sender_id, interface)
        elif choice == 'r':
            roles = {}
            for node in interface.nodes.values():
                role = node['user'].get('role', 'Unknown')
                roles[role] = roles.get(role, 0) + 1
            response = "Roles:\n" + "\n".join([f"{role}: {count}" for role, count in roles.items()])
            send_message(response, sender_id, interface)
            handle_stats_command(sender_id, interface)
        elif choice == 's':
            handle_snr_leaderboard(sender_id, interface)
            handle_stats_command(sender_id, interface)
        elif choice == 'd':
            handle_distance_records(sender_id, interface)
            handle_stats_command(sender_id, interface)
        elif choice == 'c':
            handle_channel_activity(sender_id, interface)
            handle_stats_command(sender_id, interface)
        elif choice == 't':
            handle_top_nodes(sender_id, interface)
            handle_stats_command(sender_id, interface)
        elif choice == 'p':
            handle_propagation_analysis_command(sender_id, interface)


def handle_bb_steps(sender_id, message, step, state, interface, bbs_nodes):
    boards = {0: "General", 1: "Info", 2: "News", 3: "Urgent"}
    if step == 1:
        if message.lower() == 'e':
            handle_help_command(sender_id, interface, 'bbs')
            return
        board_name = boards[int(message)]
        bulletins = get_bulletins(board_name)
        response = f"{board_name} has {len(bulletins)} messages.\n[R]ead  [P]ost"
        send_message(response, sender_id, interface)
        update_user_state(sender_id, {'command': 'BULLETIN_ACTION', 'step': 2, 'board': board_name})

    elif step == 2:
        board_name = state['board']
        if message.lower() == 'r':
            bulletins = get_bulletins(board_name)
            if bulletins:
                send_message(f"Select a bulletin number to view from {board_name}:", sender_id, interface)
                for bulletin in bulletins:
                    send_message(f"[{bulletin[0]}] {bulletin[1]}", sender_id, interface)
                update_user_state(sender_id, {'command': 'BULLETIN_READ', 'step': 3, 'board': board_name})
            else:
                send_message(f"No bulletins in {board_name}.", sender_id, interface)
                handle_bb_steps(sender_id, 'e', 1, state, interface, bbs_nodes)
        elif message.lower() == 'p':
            if board_name.lower() == 'urgent':
                node_id = get_node_id_from_num(sender_id, interface)
                allowed_nodes = interface.allowed_nodes
                logging.info(f"Checking permissions for node_id: {node_id} with allowed_nodes: {allowed_nodes}")  # Debug statement
                if allowed_nodes and node_id not in allowed_nodes:
                    send_message("You don't have permission to post to this board.", sender_id, interface)
                    handle_bb_steps(sender_id, 'e', 1, state, interface, bbs_nodes)
                    return
            send_message("What is the subject of your bulletin? Keep it short.", sender_id, interface)
            update_user_state(sender_id, {'command': 'BULLETIN_POST', 'step': 4, 'board': board_name})

    elif step == 3:
        bulletin_id = int(message)
        sender_short_name, date, subject, content, unique_id = get_bulletin_content(bulletin_id)
        send_message(f"From: {sender_short_name}\nDate: {date}\nSubject: {subject}\n- - - - - - -\n{content}", sender_id, interface)
        board_name = state['board']
        handle_bb_steps(sender_id, 'e', 1, state, interface, bbs_nodes)

    elif step == 4:
        subject = message
        send_message("Send the contents of your bulletin. Send a message with END when finished.", sender_id, interface)
        update_user_state(sender_id, {'command': 'BULLETIN_POST_CONTENT', 'step': 5, 'board': state['board'], 'subject': subject, 'content': ''})

    elif step == 5:
        if message.lower() == "end":
            board = state['board']
            subject = state['subject']
            content = state['content']
            node_id = get_node_id_from_num(sender_id, interface)
            node_info = interface.nodes.get(node_id)
            if node_info is None:
                send_message("Error: Unable to retrieve your node information.", sender_id, interface)
                update_user_state(sender_id, None)
                return
            sender_short_name = node_info['user'].get('shortName', f"Node {sender_id}")
            unique_id = add_bulletin(board, sender_short_name, subject, content, bbs_nodes, interface)
            send_message(f"Your bulletin '{subject}' has been posted to {board}.\n(╯°□°)╯📄📌[{board}]", sender_id, interface)
            handle_bb_steps(sender_id, 'e', 1, state, interface, bbs_nodes)
        else:
            state['content'] += message + "\n"
            update_user_state(sender_id, state)



def handle_mail_steps(sender_id, message, step, state, interface, bbs_nodes):
    message = message.strip()
    if len(message) == 2 and message[1] == 'x':
        message = message[0]

    if step == 1:
        choice = message.lower()
        if choice == 'r':
            sender_node_id = get_node_id_from_num(sender_id, interface)
            mail = get_mail(sender_node_id)
            if mail:
                send_message(f"You have {len(mail)} mail messages. Select a message number to read:", sender_id, interface)
                for msg in mail:
                    send_message(f"-{msg[0]}-\nDate: {msg[3]}\nFrom: {msg[1]}\nSubject: {msg[2]}", sender_id, interface)
                update_user_state(sender_id, {'command': 'MAIL', 'step': 2})
            else:
                send_message("There are no messages in your mailbox.📭", sender_id, interface)
                update_user_state(sender_id, None)
        elif choice == 's':
            send_message("What is the Short Name of the node you want to leave a message for?", sender_id, interface)
            update_user_state(sender_id, {'command': 'MAIL', 'step': 3})
        elif choice == 'x':
            handle_help_command(sender_id, interface)

    elif step == 2:
        mail_id = int(message)
        try:
            sender_node_id = get_node_id_from_num(sender_id, interface)
            sender, date, subject, content, unique_id = get_mail_content(mail_id, sender_node_id)
            send_message(f"Date: {date}\nFrom: {sender}\nSubject: {subject}\n{content}", sender_id, interface)
            send_message("What would you like to do with this message?\n[K]eep  [D]elete  [R]eply", sender_id, interface)
            update_user_state(sender_id, {'command': 'MAIL', 'step': 4, 'mail_id': mail_id, 'unique_id': unique_id, 'sender': sender, 'subject': subject, 'content': content})
        except TypeError:
            logging.info(f"Node {sender_id} tried to access non-existent message")
            send_message("Mail not found", sender_id, interface)
            update_user_state(sender_id, None)

    elif step == 3:
        short_name = message.lower()
        nodes = get_node_info(interface, short_name)
        if not nodes:
            send_message("I'm unable to find that node in my database.", sender_id, interface)
            handle_mail_command(sender_id, interface)
        elif len(nodes) == 1:
            recipient_id = nodes[0]['num']
            recipient_name = get_node_name(recipient_id, interface)
            send_message(f"What is the subject of your message to {recipient_name}?\nKeep it short.", sender_id, interface)
            update_user_state(sender_id, {'command': 'MAIL', 'step': 5, 'recipient_id': recipient_id})
        else:
            send_message("There are multiple nodes with that short name. Which one would you like to leave a message for?", sender_id, interface)
            for i, node in enumerate(nodes):
                send_message(f"[{i}] {node['longName']}", sender_id, interface)
            update_user_state(sender_id, {'command': 'MAIL', 'step': 6, 'nodes': nodes})

    elif step == 4:
        if message.lower() == "d":
            unique_id = state['unique_id']
            sender_node_id = get_node_id_from_num(sender_id, interface)
            delete_mail(unique_id, sender_node_id, bbs_nodes, interface)
            send_message("The message has been deleted 🗑️", sender_id, interface)
            update_user_state(sender_id, None)
        elif message.lower() == "r":
            sender = state['sender']
            send_message(f"Send your reply to {sender} now, followed by a message with END", sender_id, interface)
            update_user_state(sender_id, {'command': 'MAIL', 'step': 7, 'reply_to_mail_id': state['mail_id'], 'subject': f"Re: {state['subject']}", 'content': ''})
        else:
            send_message("The message has been kept in your inbox.✉️", sender_id, interface)
            update_user_state(sender_id, None)

    elif step == 5:
        subject = message
        send_message("Send your message. You can send it in multiple messages if it's too long for one.\nSend a single message with END when you're done", sender_id, interface)
        update_user_state(sender_id, {'command': 'MAIL', 'step': 7, 'recipient_id': state['recipient_id'], 'subject': subject, 'content': ''})

    elif step == 6:
        selected_node_index = int(message)
        selected_node = state['nodes'][selected_node_index]
        recipient_id = selected_node['num']
        recipient_name = get_node_name(recipient_id, interface)
        send_message(f"What is the subject of your message to {recipient_name}?\nKeep it short.", sender_id, interface)
        update_user_state(sender_id, {'command': 'MAIL', 'step': 5, 'recipient_id': recipient_id})

    elif step == 7:
        if message.lower() == "end":
            if 'reply_to_mail_id' in state:
                recipient_id = get_sender_id_by_mail_id(state['reply_to_mail_id'])  # Get the sender ID from the mail ID
            else:
                recipient_id = state.get('recipient_id')
            subject = state['subject']
            content = state['content']
            recipient_name = get_node_name(recipient_id, interface)

            sender_short_name = get_node_short_name(get_node_id_from_num(sender_id, interface), interface)
            unique_id = add_mail(get_node_id_from_num(sender_id, interface), sender_short_name, recipient_id, subject, content, bbs_nodes, interface)
            send_message(f"Mail has been posted to the mailbox of {recipient_name}.\n(╯°□°)╯📨📬", sender_id, interface)

            notification_message = f"You have a new mail message from {sender_short_name}. Check your mailbox by responding to this message with CM."
            send_message(notification_message, recipient_id, interface)

            update_user_state(sender_id, None)
            update_user_state(sender_id, {'command': 'MAIL', 'step': 8})
        else:
            state['content'] += message + "\n"
            update_user_state(sender_id, state)

    elif step == 8:
        if message.lower() == "y":
            handle_mail_command(sender_id, interface)
        else:
            send_message("Okay, feel free to send another command.", sender_id, interface)
            update_user_state(sender_id, None)


def handle_wall_of_shame_command(sender_id, interface):
    response = "Devices with battery levels below 20%:\n"
    for node_id, node in interface.nodes.items():
        metrics = node.get('deviceMetrics', {})
        battery_level = metrics.get('batteryLevel', 101)
        if battery_level < 20:
            long_name = node['user']['longName']
            response += f"{long_name} - Battery {battery_level}%\n"
    if response == "Devices with battery levels below 20%:\n":
        response = "No devices with battery levels below 20% found."
    send_message(response, sender_id, interface)


def handle_channel_directory_command(sender_id, interface):
    response = "📚CHANNEL DIRECTORY📚\nWhat would you like to do?\n[V]iew  [P]ost  E[X]IT"
    send_message(response, sender_id, interface)
    update_user_state(sender_id, {'command': 'CHANNEL_DIRECTORY', 'step': 1})


def handle_channel_directory_steps(sender_id, message, step, state, interface):
    message = message.strip()
    if len(message) == 2 and message[1] == 'x':
        message = message[0]

    if step == 1:
        choice = message
        if choice.lower() == 'x':
            handle_help_command(sender_id, interface)
            return
        elif choice.lower() == 'v':
            channels = get_channels()
            if channels:
                response = "Select a channel number to view:\n" + "\n".join(
                    [f"[{i}] {channel[0]}" for i, channel in enumerate(channels)])
                send_message(response, sender_id, interface)
                update_user_state(sender_id, {'command': 'CHANNEL_DIRECTORY', 'step': 2})
            else:
                send_message("No channels available in the directory.", sender_id, interface)
                handle_channel_directory_command(sender_id, interface)
        elif choice.lower() == 'p':
            send_message("Name your channel for the directory:", sender_id, interface)
            update_user_state(sender_id, {'command': 'CHANNEL_DIRECTORY', 'step': 3})

    elif step == 2:
        channel_index = int(message)
        channels = get_channels()
        if 0 <= channel_index < len(channels):
            channel_name, channel_url = channels[channel_index]
            send_message(f"Channel Name: {channel_name}\nChannel URL:\n{channel_url}", sender_id, interface)
        handle_channel_directory_command(sender_id, interface)

    elif step == 3:
        channel_name = message
        send_message("Send a message with your channel URL or PSK:", sender_id, interface)
        update_user_state(sender_id, {'command': 'CHANNEL_DIRECTORY', 'step': 4, 'channel_name': channel_name})

    elif step == 4:
        channel_url = message
        channel_name = state['channel_name']
        add_channel(channel_name, channel_url)
        send_message(f"Your channel '{channel_name}' has been added to the directory.", sender_id, interface)
        handle_channel_directory_command(sender_id, interface)


def handle_send_mail_command(sender_id, message, interface, bbs_nodes):
    try:
        parts = message.split(",,", 3)
        if len(parts) != 4:
            send_message("Send Mail Quick Command format:\nSM,,{short_name},,{subject},,{message}", sender_id, interface)
            return

        _, short_name, subject, content = parts
        nodes = get_node_info(interface, short_name.lower())
        if not nodes:
            send_message(f"Node with short name '{short_name}' not found.", sender_id, interface)
            return
        if len(nodes) > 1:
            send_message(f"Multiple nodes with short name '{short_name}' found. Please be more specific.", sender_id,
                         interface)
            return

        recipient_id = nodes[0]['num']
        recipient_name = get_node_name(recipient_id, interface)
        sender_short_name = get_node_short_name(get_node_id_from_num(sender_id, interface), interface)

        unique_id = add_mail(get_node_id_from_num(sender_id, interface), sender_short_name, recipient_id, subject,
                             content, bbs_nodes, interface)
        send_message(f"Mail has been sent to {recipient_name}.", sender_id, interface)

        notification_message = f"You have a new mail message from {sender_short_name}. Check your mailbox by responding to this message with CM."
        send_message(notification_message, recipient_id, interface)

    except Exception as e:
        logging.error(f"Error processing send mail command: {e}")
        send_message("Error processing send mail command.", sender_id, interface)


def handle_check_mail_command(sender_id, interface):
    try:
        sender_node_id = get_node_id_from_num(sender_id, interface)
        mail = get_mail(sender_node_id)
        if not mail:
            send_message("You have no new messages.", sender_id, interface)
            return

        response = "📬 You have the following messages:\n"
        for i, msg in enumerate(mail):
            response += f"{i + 1:02d}. From: {msg[1]}, Subject: {msg[2]}\n"
        response += "\nPlease reply with the number of the message you want to read."
        send_message(response, sender_id, interface)

        update_user_state(sender_id, {'command': 'CHECK_MAIL', 'step': 1, 'mail': mail})

    except Exception as e:
        logging.error(f"Error processing check mail command: {e}")
        send_message("Error processing check mail command.", sender_id, interface)


def handle_read_mail_command(sender_id, message, state, interface):
    try:
        mail = state.get('mail', [])
        message_number = int(message) - 1

        if message_number < 0 or message_number >= len(mail):
            send_message("Invalid message number. Please try again.", sender_id, interface)
            return

        mail_id = mail[message_number][0]
        sender_node_id = get_node_id_from_num(sender_id, interface)
        sender, date, subject, content, unique_id = get_mail_content(mail_id, sender_node_id)
        response = f"Date: {date}\nFrom: {sender}\nSubject: {subject}\n\n{content}"
        send_message(response, sender_id, interface)
        send_message("What would you like to do with this message?\n[K]eep  [D]elete  [R]eply", sender_id, interface)
        update_user_state(sender_id, {'command': 'CHECK_MAIL', 'step': 2, 'mail_id': mail_id, 'unique_id': unique_id, 'sender': sender, 'subject': subject, 'content': content})

    except ValueError:
        send_message("Invalid input. Please enter a valid message number.", sender_id, interface)
    except Exception as e:
        logging.error(f"Error processing read mail command: {e}")
        send_message("Error processing read mail command.", sender_id, interface)


def handle_delete_mail_confirmation(sender_id, message, state, interface, bbs_nodes):
    try:
        choice = message.lower().strip()
        if len(choice) == 2 and choice[1] == 'x':
            choice = choice[0]

        if choice == 'd':
            unique_id = state['unique_id']
            sender_node_id = get_node_id_from_num(sender_id, interface)
            delete_mail(unique_id, sender_node_id, bbs_nodes, interface)
            send_message("The message has been deleted 🗑️", sender_id, interface)
            update_user_state(sender_id, None)
        elif choice == 'r':
            sender = state['sender']
            send_message(f"Send your reply to {sender} now, followed by a message with END", sender_id, interface)
            update_user_state(sender_id, {'command': 'MAIL', 'step': 7, 'reply_to_mail_id': state['mail_id'], 'subject': f"Re: {state['subject']}", 'content': ''})
        else:
            send_message("The message has been kept in your inbox.✉️", sender_id, interface)
            update_user_state(sender_id, None)

    except Exception as e:
        logging.error(f"Error processing delete mail confirmation: {e}")
        send_message("Error processing delete mail confirmation.", sender_id, interface)



def handle_post_bulletin_command(sender_id, message, interface, bbs_nodes):
    try:
        parts = message.split(",,", 3)
        if len(parts) != 4:
            send_message("Post Bulletin Quick Command format:\nPB,,{board_name},,{subject},,{content}", sender_id, interface)
            return

        _, board_name, subject, content = parts
        sender_short_name = get_node_short_name(get_node_id_from_num(sender_id, interface), interface)

        unique_id = add_bulletin(board_name, sender_short_name, subject, content, bbs_nodes, interface)
        send_message(f"Your bulletin '{subject}' has been posted to {board_name}.", sender_id, interface)


    except Exception as e:
        logging.error(f"Error processing post bulletin command: {e}")
        send_message("Error processing post bulletin command.", sender_id, interface)


def handle_check_bulletin_command(sender_id, message, interface):
    try:
        # Split the message only once
        parts = message.split(",,", 1)
        if len(parts) != 2 or not parts[1].strip():
            send_message("Check Bulletins Quick Command format:\nCB,,board_name", sender_id, interface)
            return

        boards = {0: "General", 1: "Info", 2: "News", 3: "Urgent"} #list of boards
        board_name = parts[1].strip().capitalize() #get board name from quick command and capitalize it
        board_name = boards[next(key for key, value in boards.items() if value == board_name)] #search for board name in list

        bulletins = get_bulletins(board_name)
        if not bulletins:
            send_message(f"No bulletins available on {board_name} board.", sender_id, interface)
            return

        response = f"📰 Bulletins on {board_name} board:\n"
        for i, bulletin in enumerate(bulletins):
            response += f"[{i+1:02d}] Subject: {bulletin[1]}, From: {bulletin[2]}, Date: {bulletin[3]}\n"
        response += "\nPlease reply with the number of the bulletin you want to read."
        send_message(response, sender_id, interface)

        update_user_state(sender_id, {'command': 'CHECK_BULLETIN', 'step': 1, 'board_name': board_name, 'bulletins': bulletins})

    except Exception as e:
        logging.error(f"Error processing check bulletin command: {e}")
        send_message("Error processing check bulletin command.", sender_id, interface)

def handle_read_bulletin_command(sender_id, message, state, interface):
    try:
        bulletins = state.get('bulletins', [])
        message_number = int(message) - 1

        if message_number < 0 or message_number >= len(bulletins):
            send_message("Invalid bulletin number. Please try again.", sender_id, interface)
            return

        bulletin_id = bulletins[message_number][0]
        sender, date, subject, content, unique_id = get_bulletin_content(bulletin_id)
        response = f"Date: {date}\nFrom: {sender}\nSubject: {subject}\n\n{content}"
        send_message(response, sender_id, interface)

        update_user_state(sender_id, None)

    except ValueError:
        send_message("Invalid input. Please enter a valid bulletin number.", sender_id, interface)
    except Exception as e:
        logging.error(f"Error processing read bulletin command: {e}")
        send_message("Error processing read bulletin command.", sender_id, interface)


def handle_post_channel_command(sender_id, message, interface):
    try:
        parts = message.split("|", 3)
        if len(parts) != 3:
            send_message("Post Channel Quick Command format:\nCHP,,{channel_name},,{channel_url}", sender_id, interface)
            return

        _, channel_name, channel_url = parts
        bbs_nodes = interface.bbs_nodes
        add_channel(channel_name, channel_url, bbs_nodes, interface)
        send_message(f"Channel '{channel_name}' has been added to the directory.", sender_id, interface)

    except Exception as e:
        logging.error(f"Error processing post channel command: {e}")
        send_message("Error processing post channel command.", sender_id, interface)


def handle_check_channel_command(sender_id, interface):
    try:
        channels = get_channels()
        if not channels:
            send_message("No channels available in the directory.", sender_id, interface)
            return

        response = "Available Channels:\n"
        for i, channel in enumerate(channels):
            response += f"{i + 1:02d}. Name: {channel[0]}\n"
        response += "\nPlease reply with the number of the channel you want to view."
        send_message(response, sender_id, interface)

        update_user_state(sender_id, {'command': 'CHECK_CHANNEL', 'step': 1, 'channels': channels})

    except Exception as e:
        logging.error(f"Error processing check channel command: {e}")
        send_message("Error processing check channel command.", sender_id, interface)


def handle_read_channel_command(sender_id, message, state, interface):
    try:
        channels = state.get('channels', [])
        message_number = int(message) - 1

        if message_number < 0 or message_number >= len(channels):
            send_message("Invalid channel number. Please try again.", sender_id, interface)
            return

        channel_name, channel_url = channels[message_number]
        response = f"Channel Name: {channel_name}\nChannel URL: {channel_url}"
        send_message(response, sender_id, interface)

        update_user_state(sender_id, None)

    except ValueError:
        send_message("Invalid input. Please enter a valid channel number.", sender_id, interface)
    except Exception as e:
        logging.error(f"Error processing read channel command: {e}")
        send_message("Error processing read channel command.", sender_id, interface)


def handle_list_channels_command(sender_id, interface):
    try:
        channels = get_channels()
        if not channels:
            send_message("No channels available in the directory.", sender_id, interface)
            return

        response = "Available Channels:\n"
        for i, channel in enumerate(channels):
            response += f"{i+1:02d}. Name: {channel[0]}\n"
        response += "\nPlease reply with the number of the channel you want to view."
        send_message(response, sender_id, interface)

        update_user_state(sender_id, {'command': 'LIST_CHANNELS', 'step': 1, 'channels': channels})

    except Exception as e:
        logging.error(f"Error processing list channels command: {e}")
        send_message("Error processing list channels command.", sender_id, interface)


def handle_quick_help_command(sender_id, interface):
    response = ("✈️QUICK COMMANDS✈️\nSend command below for usage info:\nSM,, - Send "
                "Mail\nCM - Check Mail\nPB,, - Post Bulletin\nCB,, - Check Bulletins\n")
    send_message(response, sender_id, interface)


def handle_network_info_command(sender_id, interface):
    """Network info menu"""
    response = "📡Network Info📡\nWhat info would you like?\n[N]odes  [S]ignals  [M]esh Health  E[X]IT"
    send_message(response, sender_id, interface)
    update_user_state(sender_id, {'command': 'NETWORK_INFO', 'step': 1})


def handle_network_info_steps(sender_id, message, step, state, interface):
    """Handle network info submenu selections"""
    choice = message.lower().strip()
    if len(choice) == 2 and choice[1] == 'x':
        choice = choice[0]

    if choice == 'n':
        # Nodes online
        nodes = interface.nodes
        total_nodes = len(nodes)

        response = f"📡 Mesh Network Status 📡\n\nTotal Nodes: {total_nodes}\n\nRecent Nodes:\n"

        # Sort by last heard (most recent first)
        sorted_nodes = sorted(nodes.items(),
                            key=lambda x: x[1].get('lastHeard', 0) if isinstance(x[1], dict) else 0,
                            reverse=True)

        for i, (node_id, node_data) in enumerate(sorted_nodes[:10]):  # Show top 10
            if isinstance(node_data, dict) and 'user' in node_data:
                short_name = node_data['user'].get('shortName', 'UNK')
                long_name = node_data['user'].get('longName', 'Unknown')
                response += f"{i+1}. {short_name} - {long_name}\n"

        if total_nodes > 10:
            response += f"\n...and {total_nodes - 10} more nodes"

        send_message(response, sender_id, interface)
        update_user_state(sender_id, None)

    elif choice == 's':
        # Signal reports
        response = "📶 Signal Reports 📶\n\nRecent SNR readings:\n"

        nodes = interface.nodes
        signal_nodes = []

        for node_id, node_data in nodes.items():
            if isinstance(node_data, dict) and 'snr' in node_data:
                short_name = node_data.get('user', {}).get('shortName', 'UNK')
                snr = node_data.get('snr', 0)
                signal_nodes.append((short_name, snr))

        # Sort by SNR (best first)
        signal_nodes.sort(key=lambda x: x[1], reverse=True)

        for i, (name, snr) in enumerate(signal_nodes[:10]):
            response += f"{name}: {snr:.1f} dB\n"

        if not signal_nodes:
            response = "No signal data available yet."

        send_message(response, sender_id, interface)
        update_user_state(sender_id, None)

    elif choice == 'm':
        # Mesh health
        nodes = interface.nodes
        total = len(nodes)

        response = f"🏥 Mesh Health 🏥\n\n"
        response += f"Total Nodes: {total}\n"

        # Count by hardware type
        hw_types = {}
        for node_id, node_data in nodes.items():
            if isinstance(node_data, dict) and 'user' in node_data:
                hw = node_data['user'].get('hwModel', 'UNKNOWN')
                hw_types[hw] = hw_types.get(hw, 0) + 1

        response += f"\nHardware Types:\n"
        for hw, count in sorted(hw_types.items(), key=lambda x: x[1], reverse=True)[:5]:
            response += f"{hw}: {count}\n"

        send_message(response, sender_id, interface)
        update_user_state(sender_id, None)

    elif choice == 'x':
        handle_help_command(sender_id, interface)
    else:
        send_message("Invalid option. Please try again.", sender_id, interface)


def handle_resources_command(sender_id, interface):
    """Resources menu"""
    response = "📚Resources📚\nWhat info do you need?\n[G]uide  [H]ardware  [L]inks  [A]I Guide  E[X]IT"
    send_message(response, sender_id, interface)
    update_user_state(sender_id, {'command': 'RESOURCES', 'step': 1})


def handle_resources_steps(sender_id, message, step, state, interface):
    """Handle resources submenu selections"""
    choice = message.lower().strip()
    if len(choice) == 2 and choice[1] == 'x':
        choice = choice[0]

    if choice == 'g':
        # Getting started guide
        response = ("📖 Getting Started 📖\n\n"
                   "New to mesh?\n"
                   "• Change your node name in settings\n"
                   "• Set up channels to join groups\n"
                   "• Add friends by their node ID\n"
                   "• Adjust transmit power for range\n"
                   "• Use CLIENT role for mobile nodes\n"
                   "• Use CLIENT_MUTE for base stations\n\n"
                   "Learn more: meshtastic.org/docs")
        send_message(response, sender_id, interface)
        update_user_state(sender_id, None)

    elif choice == 'h':
        # Hardware recommendations
        response = ("🔧 Recommended Hardware 🔧\n\n"
                   "Portable:\n"
                   "- Heltec V3 ($30)\n"
                   "- T-Beam ($40)\n"
                   "- RAK WisBlock ($50)\n\n"
                   "Base Station:\n"
                   "- Station G2 ($100)\n"
                   "- RAK Base ($100)\n\n"
                   "Tracker:\n"
                   "- T1000-E ($30-40)\n\n"
                   "Info: meshtastic.org/docs/hardware")
        send_message(response, sender_id, interface)
        update_user_state(sender_id, None)

    elif choice == 'l':
        # Useful links
        response = ("🔗 Useful Links 🔗\n\n"
                   "Main Site:\n"
                   "meshtastic.org\n\n"
                   "Documentation:\n"
                   "meshtastic.org/docs\n\n"
                   "Discord:\n"
                   "discord.gg/meshtastic\n\n"
                   "Reddit:\n"
                   "r/meshtastic")
        send_message(response, sender_id, interface)
        update_user_state(sender_id, None)

    elif choice == 'a':
        # AI Guide - NotebookLM
        response = ("🤖 AI Meshtastic Guide 🤖\n\n"
                   "Interactive AI assistant for Meshtastic help.\n\n"
                   "notebooklm.google.com/notebook/ebf5b4fd-2074-4160-a9c9-996155209bb8")
        send_message(response, sender_id, interface)
        update_user_state(sender_id, None)

    elif choice == 'x':
        handle_help_command(sender_id, interface)
    else:
        send_message("Invalid option. Please try again.", sender_id, interface)



def handle_weather_command(sender_id, interface):
    """Prompt user for zip code"""
    response = "☁️ Weather ☁️\n\nEnter your 5-digit ZIP code:"
    send_message(response, sender_id, interface)
    update_user_state(sender_id, {'command': 'WEATHER', 'step': 1})


def handle_weather_steps(sender_id, message, step, state, interface):
    """Handle weather zip code input"""
    if step == 1:
        zip_code = message.strip()

        # Validate zip code
        if not zip_code.isdigit() or len(zip_code) != 5:
            send_message("Invalid ZIP code. Please enter a 5-digit ZIP code.", sender_id, interface)
            return

        try:
            api_key = "b5f7bc717799c13af6c652a35002edd6"
            country_code = "us"

            # Get current weather
            url = f"http://api.openweathermap.org/data/2.5/weather?zip={zip_code},{country_code}&appid={api_key}&units=imperial"
            response = requests.get(url, timeout=5)

            if response.status_code == 200:
                data = response.json()

                temp = data["main"]["temp"]
                feels_like = data["main"]["feels_like"]
                humidity = data["main"]["humidity"]
                conditions = data["weather"][0]["description"].title()
                city = data["name"]
                state_code = data.get("sys", {}).get("country", "")

                weather_msg = (f"☁️ {city} Weather ☁️\n\n"
                              f"Temp: {temp:.0f}°F (feels {feels_like:.0f}°F)\n"
                              f"Conditions: {conditions}\n"
                              f"Humidity: {humidity}%")

                send_message(weather_msg, sender_id, interface)
            elif response.status_code == 404:
                send_message("ZIP code not found. Please try again.", sender_id, interface)
            else:
                send_message("Unable to get weather at this time.", sender_id, interface)

        except Exception as e:
            logging.error(f"Error getting weather: {e}")
            send_message("Weather service unavailable.", sender_id, interface)

        update_user_state(sender_id, None)



# ========== TRIVIA GAME ==========
def handle_trivia_command(sender_id, interface):
    """Start trivia game"""
    try:
        with open('trivia.txt', 'r') as file:
            questions = [line.strip() for line in file.readlines() if line.strip()]

        if not questions:
            send_message("No trivia questions available.", sender_id, interface)
            return

        question_line = random.choice(questions)
        parts = question_line.split('|')
        question = parts[0]
        answer = parts[1]
        category = parts[2] if len(parts) > 2 else 'A'

        response = f"🎯 Meshtastic Trivia 🎯\n\n{question}\n\nReply with your answer!"
        send_message(response, sender_id, interface)
        update_user_state(sender_id, {'command': 'TRIVIA', 'step': 1, 'answer': answer})
    except Exception as e:
        logging.error(f"Error loading trivia: {e}")
        send_message("Trivia game unavailable.", sender_id, interface)


def handle_trivia_steps(sender_id, message, step, state, interface):
    """Handle trivia answer"""
    if step == 1:
        user_answer = message.strip()
        correct_answer = state['answer']

        # Fuzzy matching - check if answer is mostly correct
        if user_answer.lower() in correct_answer.lower() or correct_answer.lower() in user_answer.lower():
            send_message(f"✅ Correct! The answer is: {correct_answer}\n\nPlay again? Send 'T' or type 'X' for menu.", sender_id, interface)
        else:
            send_message(f"❌ Not quite! The answer was: {correct_answer}\n\nTry another? Send 'T' or type 'X' for menu.", sender_id, interface)

        update_user_state(sender_id, None)


# ========== GAMES MENU ==========
def handle_games_command(sender_id, interface):
    """Games menu"""
    response = "🎮 Games Menu 🎮\nWhat would you like to play?\n[T]rivia  [P]ropagation Calc  E[X]IT"
    send_message(response, sender_id, interface)
    update_user_state(sender_id, {'command': 'GAMES', 'step': 1})


def handle_games_steps(sender_id, message, step, state, interface):
    """Handle games menu selections"""
    message = message.lower().strip()

    if message == 'x':
        handle_help_command(sender_id, interface)
        return
    elif message == 't':
        handle_trivia_command(sender_id, interface)
    elif message == 'p':
        handle_propagation_command(sender_id, interface)
    else:
        handle_games_command(sender_id, interface)


# ========== PROPAGATION CALCULATOR ==========
def handle_propagation_command(sender_id, interface):
    """Propagation calculator"""
    response = "📡 Propagation Calculator 📡\n\nEnter antenna height in feet (e.g., 20):"
    send_message(response, sender_id, interface)
    update_user_state(sender_id, {'command': 'PROPAGATION', 'step': 1})


def handle_propagation_steps(sender_id, message, step, state, interface):
    """Handle propagation calculator"""
    try:
        if step == 1:
            height_ft = float(message.strip())
            # Radio horizon formula: distance (miles) ≈ 1.23 × √height_feet
            distance_miles = 1.23 * (height_ft ** 0.5)

            # Fresnel zone clearance
            if height_ft < 10:
                condition = "Poor - obstacles likely"
            elif height_ft < 30:
                condition = "Fair - some obstacles"
            elif height_ft < 100:
                condition = "Good - clear path likely"
            else:
                condition = "Excellent - long range possible"

            response = (f"📡 Estimated Range 📡\n\n"
                       f"Antenna: {height_ft:.0f} ft\n"
                       f"Line of Sight: ~{distance_miles:.1f} mi\n"
                       f"Condition: {condition}\n\n"
                       f"Note: Actual range varies with terrain, weather, and obstacles.")

            send_message(response, sender_id, interface)
            update_user_state(sender_id, None)
    except ValueError:
        send_message("Please enter a valid number.", sender_id, interface)
        update_user_state(sender_id, None)


# ========== ENHANCED STATS ==========
def handle_snr_leaderboard(sender_id, interface):
    """Show SNR leaderboard"""
    try:
        # Collect SNR data from nodes
        snr_data = []
        for node_id, node_info in interface.nodes.items():
            if 'snr' in node_info:
                snr = node_info['snr']
                name = node_info['user'].get('longName', 'Unknown')
                short_name = node_info['user'].get('shortName', 'Unknown')
                snr_data.append((snr, short_name, name))

        if not snr_data:
            send_message("No SNR data available yet.", sender_id, interface)
            return

        # Sort by SNR (best first)
        snr_data.sort(reverse=True, key=lambda x: x[0])

        # Top 10
        response = "📶 SNR Leaderboard 📶\n\nBest Signals:\n"
        for i, (snr, short, name) in enumerate(snr_data[:10], 1):
            response += f"{i}. {short} - {snr:.1f} dB\n"

        send_message(response, sender_id, interface)
    except Exception as e:
        logging.error(f"Error generating SNR leaderboard: {e}")
        send_message("Error generating leaderboard.", sender_id, interface)


def handle_distance_records(sender_id, interface):
    """Show distance records"""
    try:
        import math

        # Get our position
        my_node = interface.nodes.get(interface.myInfo.my_node_num)
        if not my_node or 'position' not in my_node:
            send_message("GPS position not available.", sender_id, interface)
            return

        my_lat = my_node['position'].get('latitude')
        my_lon = my_node['position'].get('longitude')

        if my_lat is None or my_lon is None:
            send_message("GPS position not available.", sender_id, interface)
            return

        # Calculate distances
        distances = []
        for node_id, node_info in interface.nodes.items():
            if node_id == interface.myInfo.my_node_num:
                continue

            if 'position' in node_info:
                lat = node_info['position'].get('latitude')
                lon = node_info['position'].get('longitude')

                if lat and lon:
                    # Haversine formula
                    R = 3959  # Earth radius in miles
                    lat1, lon1 = math.radians(my_lat), math.radians(my_lon)
                    lat2, lon2 = math.radians(lat), math.radians(lon)

                    dlat = lat2 - lat1
                    dlon = lon2 - lon1

                    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
                    c = 2 * math.asin(math.sqrt(a))
                    distance = R * c

                    name = node_info['user'].get('shortName', 'Unknown')
                    distances.append((distance, name))

        if not distances:
            send_message("No distance data available.", sender_id, interface)
            return

        # Sort by distance (farthest first)
        distances.sort(reverse=True)

        response = "🌍 Distance Records 🌍\n\nFarthest Nodes:\n"
        for i, (dist, name) in enumerate(distances[:10], 1):
            response += f"{i}. {name} - {dist:.1f} mi\n"

        send_message(response, sender_id, interface)
    except Exception as e:
        logging.error(f"Error calculating distances: {e}")
        send_message("Error calculating distances.", sender_id, interface)


def handle_channel_activity(sender_id, interface):
    """Show channel activity stats"""
    try:
        from db_operations import get_channel_activity_stats, get_message_stats

        # Get stats for last 24 hours
        channel_stats = get_channel_activity_stats(hours=24)
        msg_stats = get_message_stats(hours=24)

        response = "📻 Channel Activity (24h) 📻\n\n"
        response += f"Total Messages: {msg_stats['total']}\n"
        response += f"Avg SNR: {msg_stats['avg_snr']:.1f} dB\n\n"

        if channel_stats:
            response += "Messages by Channel:\n"
            channel_names = {0: "Primary", 1: "Secondary 1", 2: "Secondary 2", 3: "Secondary 3"}
            for channel_idx, count in channel_stats:
                channel_name = channel_names.get(channel_idx, f"Channel {channel_idx}")
                response += f"- {channel_name}: {count} msgs\n"
        else:
            response += "No channel data yet.\n"

        if msg_stats['top_senders']:
            response += f"\nTop Senders:\n"
            for i, (sender, count) in enumerate(msg_stats['top_senders'][:5], 1):
                response += f"{i}. {sender}: {count}\n"

        send_message(response, sender_id, interface)
    except Exception as e:
        logging.error(f"Error showing channel activity: {e}")
        send_message("Error retrieving channel activity.", sender_id, interface)


def handle_top_nodes(sender_id, interface):
    """Show most active nodes"""
    try:
        # Sort by lastHeard to find most recently active
        recent_nodes = []
        current_time = int(time.time())

        for node_id, node_info in interface.nodes.items():
            if 'lastHeard' in node_info:
                last_heard = node_info['lastHeard']
                minutes_ago = (current_time - last_heard) / 60
                name = node_info['user'].get('shortName', 'Unknown')
                snr = node_info.get('snr', 0)
                recent_nodes.append((last_heard, name, snr, minutes_ago))

        if not recent_nodes:
            send_message("No activity data available.", sender_id, interface)
            return

        # Sort by most recent
        recent_nodes.sort(reverse=True)

        response = "⭐ Most Active Nodes ⭐\n\nRecent Activity:\n"
        for i, (timestamp, name, snr, mins) in enumerate(recent_nodes[:10], 1):
            if mins < 1:
                time_str = "Just now"
            elif mins < 60:
                time_str = f"{mins:.0f}m ago"
            else:
                time_str = f"{mins/60:.1f}h ago"
            response += f"{i}. {name} - {time_str}\n"

        send_message(response, sender_id, interface)
    except Exception as e:
        logging.error(f"Error finding top nodes: {e}")
        send_message("Error finding active nodes.", sender_id, interface)


# ========== PROPAGATION ANALYSIS ==========
def handle_propagation_analysis_command(sender_id, interface):
    """Propagation analysis menu"""
    response = ("📊 Propagation Analysis 📊\n"
                "[H]ourly Trends\n"
                "[B]est/Worst Times\n"
                "[N]ode Reliability\n"
                "E[X]IT")
    send_message(response, sender_id, interface)
    update_user_state(sender_id, {'command': 'PROP_ANALYSIS', 'step': 1})


def handle_propagation_analysis_steps(sender_id, message, step, state, interface):
    """Handle propagation analysis menu"""
    message = message.lower().strip()
    
    if message == 'x':
        handle_help_command(sender_id, interface)
        return
    elif message == 'h':
        # Hourly trends
        from db_operations import get_hourly_propagation_stats
        hourly = get_hourly_propagation_stats()
        
        if hourly:
            response = "📡 Best Times to Mesh 📡\n\nAvg SNR by Hour (7 days):\n"
            for hour, avg_snr, avg_rssi, count in hourly:
                # Convert to 12-hour format
                hour_int = int(hour)
                ampm = "AM" if hour_int < 12 else "PM"
                hour_12 = hour_int if hour_int <= 12 else hour_int - 12
                hour_12 = 12 if hour_12 == 0 else hour_12
                
                response += f"{hour_12:2d}{ampm}: {avg_snr:+.1f}dB ({count}msg)\n"
            send_message(response, sender_id, interface)
        else:
            send_message("Not enough data yet. Check back after a few days!", sender_id, interface)
        
        handle_propagation_analysis_command(sender_id, interface)
        
    elif message == 'b':
        # Best/worst conditions
        from db_operations import get_best_worst_conditions
        conditions = get_best_worst_conditions()
        
        response = "🏆 Propagation Records 🏆\n\nBest SNR (7 days):\n"
        for name, snr, timestamp in conditions['best'][:5]:
            response += f"{name}: {snr:+.1f}dB\n"
        
        response += "\n📉 Weakest Signals:\n"
        for name, snr, timestamp in conditions['worst'][:5]:
            response += f"{name}: {snr:+.1f}dB\n"
        
        send_message(response, sender_id, interface)
        handle_propagation_analysis_command(sender_id, interface)
        
    elif message == 'n':
        # Ask for node to analyze
        send_message("Enter node short name to analyze (e.g., 4B80):", sender_id, interface)
        update_user_state(sender_id, {'command': 'PROP_NODE_INPUT', 'step': 1})
    else:
        handle_propagation_analysis_command(sender_id, interface)


def handle_prop_node_input_steps(sender_id, message, step, state, interface):
    """Handle node reliability lookup"""
    from db_operations import get_node_reliability
    import time
    
    # Find node by short name
    node_id = None
    for nid, node_info in interface.nodes.items():
        if node_info['user'].get('shortName', '').lower() == message.lower().strip():
            node_id = nid
            break
    
    if node_id:
        stats = get_node_reliability(node_id)
        if stats['message_count'] > 0:
            response = (f"📊 {message.upper()} Reliability 📊\n\n"
                       f"Messages (7d): {stats['message_count']}\n"
                       f"Avg SNR: {stats['avg_snr']:+.1f}dB\n"
                       f"Range: {stats['min_snr']:+.1f} to {stats['max_snr']:+.1f}dB\n"
                       f"Avg RSSI: {stats['avg_rssi']:.0f}dBm\n\n"
                       f"Signal Quality: {'Excellent' if stats['avg_snr'] > 5 else 'Good' if stats['avg_snr'] > 0 else 'Fair'}")
        else:
            response = f"No data for {message.upper()} in last 7 days."
    else:
        response = f"Node '{message}' not found."
    
    send_message(response, sender_id, interface)
    update_user_state(sender_id, None)
