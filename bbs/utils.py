import logging
import time

user_states = {}


def update_user_state(user_id, state):
    user_states[user_id] = state


def get_user_state(user_id):
    return user_states.get(user_id, None)


def send_message(message, destination, interface, response_timestamp=None):
    max_payload_size = 200

    # Check if interface has a request_timestamp from the incoming message
    if response_timestamp is None and hasattr(interface, 'request_timestamp') and interface.request_timestamp:
        response_timestamp = interface.request_timestamp

    for i in range(0, len(message), max_payload_size):
        chunk = message[i:i + max_payload_size]

        # Use provided timestamp (from incoming message) or current time
        # Add 1 second to response_timestamp so response appears after the request
        send_timestamp = (response_timestamp + 1) if response_timestamp else int(time.time())

        try:
            d = interface.sendText(
                text=chunk,
                destinationId=destination,
                wantAck=True,
                wantResponse=False
            )
            destid = get_node_id_from_num(destination, interface)
            chunk_display = chunk.replace('\n', '\\n')
            logging.info(f"Sending message to user '{get_node_short_name(destid, interface)}' ({destid}) with sendID {d.id}: \"{chunk_display}\"")

            # Log outgoing BBS response to database
            try:
                from db_operations import log_message  # Import here to avoid circular import

                # Get BBS node info
                bbs_node = interface.getMyNodeInfo()
                bbs_node_id = bbs_node.get('user', {}).get('id', 'unknown')
                bbs_short_name = bbs_node.get('user', {}).get('shortName', 'BBS')

                # Log the outgoing message with the timestamp from BEFORE sending
                log_message(
                    sender_id=bbs_node_id,
                    sender_short_name=bbs_short_name,
                    to_id=destid if destid else 'unknown',
                    message=chunk,
                    timestamp=send_timestamp,  # Use timestamp from before send
                    channel_index=0,  # Assume primary channel
                    snr=None,  # No SNR for outgoing
                    rssi=None,  # No RSSI for outgoing
                    hop_limit=None
                )
            except Exception as log_error:
                logging.warning(f"Failed to log outgoing message to database: {log_error}")

        except Exception as e:
            logging.info(f"REPLY SEND ERROR {e.message}")


        time.sleep(2)


def get_node_info(interface, short_name):
    nodes = [{'num': node_id, 'shortName': node['user']['shortName'], 'longName': node['user']['longName']}
             for node_id, node in interface.nodes.items()
             if node['user']['shortName'].lower() == short_name]
    return nodes


def get_node_id_from_num(node_num, interface):
    for node_id, node in interface.nodes.items():
        if node['num'] == node_num:
            return node_id
    return None


def get_node_short_name(node_id, interface):
    node_info = interface.nodes.get(node_id)
    if node_info:
        return node_info['user']['shortName']
    return None


def send_bulletin_to_bbs_nodes(board, sender_short_name, subject, content, unique_id, bbs_nodes, interface):
    message = f"BULLETIN|{board}|{sender_short_name}|{subject}|{content}|{unique_id}"
    for node_id in bbs_nodes:
        send_message(message, node_id, interface)


def send_mail_to_bbs_nodes(sender_id, sender_short_name, recipient_id, subject, content, unique_id, bbs_nodes,
                           interface):
    message = f"MAIL|{sender_id}|{sender_short_name}|{recipient_id}|{subject}|{content}|{unique_id}"
    logging.info(f"SERVER SYNC: Syncing new mail message {subject} sent from {sender_short_name} to other BBS systems.")
    for node_id in bbs_nodes:
        send_message(message, node_id, interface)


def send_delete_bulletin_to_bbs_nodes(bulletin_id, bbs_nodes, interface):
    message = f"DELETE_BULLETIN|{bulletin_id}"
    for node_id in bbs_nodes:
        send_message(message, node_id, interface)


def send_delete_mail_to_bbs_nodes(unique_id, bbs_nodes, interface):
    message = f"DELETE_MAIL|{unique_id}"
    logging.info(f"SERVER SYNC: Sending delete mail sync message with unique_id: {unique_id}")
    for node_id in bbs_nodes:
        send_message(message, node_id, interface)


def send_channel_to_bbs_nodes(name, url, bbs_nodes, interface):
    message = f"CHANNEL|{name}|{url}"
    for node_id in bbs_nodes:
        send_message(message, node_id, interface)
