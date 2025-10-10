import sqlite3
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from datetime import datetime, timedelta

# --- Configuration: REPLACE THESE PLACEHOLDERS WITH ACTUAL WHATSAPP NUMBERS ---
# NOTE: Numbers must be in E.164 format (e.g., '+15551234567')

ROLES = {
    'MANAGER': os.environ.get("MANAGER"), 
    'TAILOR_1': os.environ.get("TAILOR_1"),
    'TAILOR_2': os.environ.get("TAILOR_2"),
    'SALES_GUY': os.environ.get("SALES_GUY")
}

app = Flask(__name__)
DATABASE_NAME = 'orders.db'

# --- Database Setup ---

def get_db_connection():
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_name TEXT NOT NULL,
            fabric_type TEXT,
            size TEXT,
            color TEXT,
            garment_type TEXT,
            special_notes TEXT,
            tiktok_link TEXT,
            job_in_date TEXT NOT NULL,
            job_out_date TEXT,
            status TEXT NOT NULL,
            priority_score INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

# Initialize the database on startup
init_db()

# --- Role Check Function ---

def get_user_role(from_number):
    for role, numbers in ROLES.items():
        if from_number in numbers:
            return role
    return 'GUEST'

# --- WhatsApp Webhook ---

@app.route("/whatsapp", methods=['POST'])
def whatsapp_webhook():
    """Handles incoming WhatsApp messages."""
    msg = request.form.get('Body', '').strip()
    from_number = request.form.get('From')
    role = get_user_role(from_number)
    resp = MessagingResponse()

    if role == 'GUEST':
        resp.message("🚫 *Access Denied*. Your number is not registered for any role.")
        return str(resp)

    # Convert message to lowercase for command matching
    command = msg.lower().split()[0] if msg else ''

    # --- SALES GUY COMMAND: !order ---
    if command == '!order' and role == 'SALES_GUY':
        try:
            # Expected format: !order Name;Fabric;Size;Color;Garment;Link;Notes
            parts = msg[len('!order'):].strip().split(';')
            if len(parts) < 7:
                raise ValueError("Missing fields")
            
            # Pad with empty strings if fewer than 7 parts
            client_name, fabric_type, size, color, garment_type, tiktok_link, special_notes = [p.strip() for p in parts[:7]]
            job_in_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            conn = get_db_connection()
            cursor = conn.execute("""
                INSERT INTO orders (client_name, fabric_type, size, color, garment_type, tiktok_link, special_notes, job_in_date, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
            """, (client_name, fabric_type, size, color, garment_type, tiktok_link, special_notes, job_in_date))
            order_id = cursor.lastrowid
            conn.commit()
            conn.close()

            response_msg = f"✅ *New Order #{order_id} Created!* \nClient: {client_name}\nGarment: {garment_type} ({color} {size})"
            resp.message(response_msg)

        except Exception as e:
            resp.message("❌ *Order Error*: Format should be `!order Name;Fabric;Size;Color;Garment;Link;Notes` (7 items separated by semicolons).")
    
    # --- TAILOR COMMANDS: !start, !complete ---
    elif (command == '!start' or command == '!complete') and role == 'TAILOR':
        try:
            order_id = int(msg.split()[1])
            new_status = 'IN PROGRESS' if command == '!start' else 'COMPLETE'
            
            conn = get_db_connection()
            conn.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
            conn.commit()
            conn.close()

            if new_status == 'COMPLETE':
                resp.message(f"✂️ *Order #{order_id} complete!* Great work. The client will be notified soon.")
            else:
                resp.message(f"🧵 *Order #{order_id} is now IN PROGRESS.* Time to get sewing!")

        except (IndexError, ValueError):
            resp.message(f"❌ *Command Error*: Please specify the Order ID. E.g., `{command} 101`")
        except sqlite3.Error:
            resp.message("❌ *Database Error*: Could not find or update the order.")


    # --- MANAGER COMMAND: !prioritize ---
    elif command == '!prioritize' and role == 'MANAGER':
        try:
            client_name_query = msg[len('!prioritize'):].strip()
            conn = get_db_connection()
            
            # 1. Try to find the order by client name
            # Only prioritize orders that are not COMPLETE or COLLECTED
            orders = conn.execute("SELECT id, client_name, status, job_out_date FROM orders WHERE client_name LIKE ? AND status NOT IN ('COMPLETE', 'COLLECTED')", ('%' + client_name_query + '%',)).fetchall()

            if orders:
                # Update priority for found orders
                order_ids = [str(o['id']) for o in orders]
                conn.execute(f"UPDATE orders SET priority_score = 1, status = 'PRIORITIZED' WHERE id IN ({','.join(order_ids)})")
                conn.commit()
                
                resp.message(f"🔥 *PRIORITY ALERT!* Orders {', '.join(order_ids)} for '{client_name_query}' have been set to PRIORITIZED.")
            else:
                # 2. If no name match, list URGENT/OVERDUE orders.
                
                # Orders that missed their deadline (not COMPLETE/COLLECTED)
                today_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                missed_deadline_orders = conn.execute("""
                    SELECT id, client_name, job_out_date, garment_type FROM orders 
                    WHERE job_out_date < ? AND status NOT IN ('COMPLETE', 'COLLECTED')
                    ORDER BY job_out_date ASC
                """, (today_date,)).fetchall()

                # Orders that are complete but not collected
                uncollected_orders = conn.execute("""
                    SELECT id, client_name, job_out_date, garment_type FROM orders 
                    WHERE status = 'COMPLETE'
                    ORDER BY job_out_date ASC
                """).fetchall()

                response_msg = "⚠️ *PRIORITY LIST: NO CLIENT MATCH FOUND.*\n\n"
                
                if missed_deadline_orders:
                    response_msg += "🛑 *OVERDUE - MISSED DEADLINE* 🛑\n(Orders not complete, deadline passed)\n"
                    for order in missed_deadline_orders:
                        response_msg += f"  - *ID #{order['id']}* ({order['client_name']}) - Garment: {order['garment_type']} - DUE: {order['job_out_date']}\n"
                    response_msg += "\n"

                if uncollected_orders:
                    response_msg += "📦 *OVERDUE - READY/UNCOLLECTED* 📦\n(Orders complete, awaiting pickup)\n"
                    for order in uncollected_orders:
                        response_msg += f"  - *ID #{order['id']}* ({order['client_name']}) - Garment: {order['garment_type']} - DONE: {order['job_out_date'] or 'N/A'}\n"
                    response_msg += "\n"

                if not missed_deadline_orders and not uncollected_orders:
                    response_msg = "✅ *NO URGENT OR OVERDUE ORDERS.* The shop pipeline is clear."

                resp.message(response_msg)

            conn.close()
            
        except IndexError:
            resp.message("❌ *Command Error*: Please specify a client name. E.g., `!prioritize Jane Doe`")
        except Exception as e:
            # A more specific error handling is in place, but keeping a general one for safety
            resp.message(f"❌ *An unexpected error occurred*: {e}")


    # --- NEW COMMAND: !collected (Sales Guy / Manager) ---
    elif command == '!collected' and role in ['SALES_GUY', 'MANAGER']:
        try:
            order_id = int(msg.split()[1])
            conn = get_db_connection()
            conn.execute("UPDATE orders SET status = 'COLLECTED' WHERE id = ?", (order_id,))
            conn.commit()
            conn.close()

            resp.message(f"💵 *Order #{order_id} marked as COLLECTED.* Transaction complete. 🎉")

        except (IndexError, ValueError):
            resp.message("❌ *Command Error*: Please specify the Order ID. E.g., `!collected 101`")
        except sqlite3.Error:
            resp.message("❌ *Database Error*: Could not find or update the order.")
            
# --- Remember to update the !pending command to exclude 'COLLECTED' orders from its list as well. ---


    # --- ALL ROLES COMMAND: !pending ---
    elif command == '!pending':
        conn = get_db_connection()
        pending_orders = conn.execute("SELECT id, client_name, garment_type, special_notes, tiktok_link FROM orders WHERE status = 'PENDING' OR status = 'PRIORITIZED' ORDER BY priority_score DESC, job_in_date ASC").fetchall()
        conn.close()
        
        if pending_orders:
            response_msg = "📋 *PENDING/PRIORITIZED JOBS:*\n\n"
            for order in pending_orders:
                status_icon = "🔥" if order['id'] in [int(o['id']) for o in conn.execute("SELECT id FROM orders WHERE status = 'PRIORITIZED'").fetchall()] else "⏳"
                response_msg += (
                    f"{status_icon} *ID #{order['id']}* ({order['client_name']})\n"
                    f"  - Garment: {order['garment_type']}\n"
                    f"  - Notes: {order['special_notes'] or 'None'}\n"
                    f"  - Link: {order['tiktok_link']}\n\n"
                )
        else:
            response_msg = "🎉 *No pending orders!* Everything is in progress or complete."
            
        resp.message(response_msg)


    # --- HELP/UNKNOWN COMMAND ---
    else:
        help_message = f"*Your Role: {role}*\n\n*Available Commands:*\n"
        if role == 'SALES_GUY':
            help_message += "`!order Name;Fabric;Size;Color;Garment;Link;Notes` - Create new order.\n"
        if role == 'TAILOR':
            help_message += "`!start [ID]` - Move order to 'IN PROGRESS'.\n`!complete [ID]` - Mark order as 'COMPLETE'.\n"
        if role == 'MANAGER':
            help_message += "`!prioritize [Name]` - Prioritize by name. If no match, see urgent orders.\n"
        
        # Command for everyone
        help_message += "`!pending` - List all waiting or prioritized orders."
        resp.message(help_message)

    return str(resp)

if __name__ == "__main__":
    app.run(debug=True)
