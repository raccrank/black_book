import os
import sqlite3
from datetime import datetime
import re
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

# --- 1. CONFIGURATION (Replace with your actual numbers) ---
# NOTE: These roles are populated by environment variables on deployment.
# Example: MANAGER = +12025550100
ROLES = {
    'MANAGER': os.environ.get("MANAGER"), 
    'SALES_GUY': os.environ.get("SALES_GUY"), 
    'TAILOR_1': os.environ.get("TAILOR_1"),
    'TAILOR_2': os.environ.get("TAILOR_2"),
}

app = Flask(__name__)
DB_NAME = 'tms.db'


# --- 2. DATABASE FUNCTIONS ---

def get_db_connection():
    """Establishes a connection to the SQLite database."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes the database structure if it doesn't exist."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Create ORDERS table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_name TEXT NOT NULL,
            garment_type TEXT NOT NULL,
            fabric_type TEXT,
            job_in_date TEXT NOT NULL,
            job_out_date TEXT NOT NULL,
            status TEXT NOT NULL,
            materials_needed TEXT,
            priority_score INTEGER DEFAULT 0
        )
    """)

    # Create STORE (Inventory) table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS store (
            material TEXT PRIMARY KEY,
            quantity REAL NOT NULL,
            unit TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


# --- 3. HELPER FUNCTIONS ---

# --- 3. HELPER FUNCTIONS (FIXED) ---

def get_user_role(from_number: str) -> str:
    """
    Determines the user's role based on their phone number.
    
    The incoming 'from_number' from Twilio starts with 'whatsapp:'.
    The numbers stored in the 'ROLES' dictionary also start with 'whatsapp:'.
    """
    # 1. Strip the 'whatsapp:' prefix from the incoming message number 
    # to get the clean +E.164 number.
    clean_from_number = from_number.replace("whatsapp:", "")
    
    # 2. Iterate through roles and check if the clean number matches
    # the number stored in the ROLES dictionary (which contains 'whatsapp:+E.164')
    for role, number in ROLES.items():
        # Clean the stored number as well, to ensure a clean +E.164 vs clean +E.164 comparison
        clean_stored_number = number.replace("whatsapp:", "") if number else None
        
        if clean_from_number == clean_stored_number:
            return role
            
    return 'GUEST'

def get_time_estimate(required_qty_meters: float) -> float:
    """
    Provides a simple time estimate based on fabric quantity.
    Example: 1 meter = 5 hours
    """
    HOURS_PER_METER = 5.0 
    return required_qty_meters * HOURS_PER_METER


# --- 4. FLASK WEBHOOK ROUTE ---

@app.route("/whatsapp", methods=['POST'])
def whatsapp_webhook() -> str:
    """Handles incoming WhatsApp messages."""
    msg = request.form.get('Body', '').strip()
    from_number = request.form.get('From')
    if from_number is None:
        resp = MessagingResponse()
        resp.message("🚫 *Access Denied*. No sender number found.")
        return str(resp)
    role = get_user_role(from_number)
    resp = MessagingResponse()

    if role == 'GUEST':
        resp.message("🚫 *Access Denied*. Your number is not registered for any role.")
        return str(resp)

    # 1. Check if the message is a single number response to a menu
    try:
        command_choice = int(msg.strip())
        is_menu_choice = True
    except ValueError:
        command_choice = None
        is_menu_choice = False

    # Get the first word for text command matching (e.g., "new", "start")
    command = msg.lower().split()[0] if msg and not is_menu_choice else ''
    
    
    # --- COMMAND ROUTING FROM MENU CHOICE (Sends instructions) ---
    
    # Universal Commands (Menu Options 1-4)
    if is_menu_choice and command_choice == 1:
        # Route to Order Creation Step 1
        resp.message(
            "📝 *NEW ORDER: START*\n"
            "Please provide the order details in one message, separating each field with a **pipe symbol (|)**.\n\n"
            "The expected format is a numbered list:\n"
            "1. Client Name\n"
            "2. Garment Type\n"
            "3. Fabric Type\n"
            "4. Quantity Needed (e.g., *3m* or *5.5 yards*)\n"
            "5. Job Out Date (*YYYY-MM-DD*)\n\n"
            "*Example: new 1. John Doe|2. 3 Piece Suit|3. Wool Cashmere|4. 6.5m|5. 2025-12-15*"
        )
        return str(resp)

    elif is_menu_choice and command_choice == 2:
        # Route to Pending Orders
        command = 'pending' # Triggers the 'pending' logic block below
    
    elif is_menu_choice and command_choice == 3:
        # Route to Stock Check
        resp.message("📦 *STOCK CHECK*\nSend `stock [material name]` to check inventory.\nExample: `stock silk` or just `stock` for the full list.")
        return str(resp)
        
    elif is_menu_choice and command_choice == 4:
        pass
        # Route to Query Tool
    elif is_menu_choice and command_choice == 5 and role in ['TAILOR_1', 'TAILOR_2']:
        resp.message(
            "🧵 *JOB ACTIONS*\n"
            "▶️ Send `start [ID]` to begin working (Status: IN PROGRESS).\n"
            "✅ Send `complete [ID]` to mark an order as finished (Status: COMPLETE)."
        )
        return str(resp)
        
    elif is_menu_choice and command_choice == 5 and role == 'MANAGER':
        resp.message("🔥 *PRIORITIZE*\nSend `prioritize [Client Name]` to mark orders as urgent, or just `prioritize` to list overdue jobs.")
        return str(resp)
        
    elif is_menu_choice and command_choice == 6 and role in ['MANAGER', 'SALES_GUY']:
        resp.message("💰 *COLLECTED*\nSend `collected [ID]` to mark an order as paid and picked up.")
        return str(resp)

    elif is_menu_choice and command_choice == 7 and role in ['MANAGER', 'SALES_GUY']:
        resp.message("➕ *ADD STOCK*\nSend `addstock [Material] | [Quantity] | [Unit]` to update inventory.\nExample: `addstock Linen | 100 | meters`")
        return str(resp)

    elif is_menu_choice and command_choice == 7 and role in ['MANAGER', 'SALES_GUY']:
        resp.message("➕ *ADD STOCK*\nSend `addstock [Material] | [Quantity] | [Unit]` to update inventory.\nExample: `addstock Linen | 100 | meters`")
        return str(resp)
        
    
    # --- COMMAND LOGIC (Executes the text commands like 'new', 'start', etc.) ---
    
    # --- NEW ORDER CREATION ---
   
    if command == 'new':
        try:
            # 1. Remove the "new" command word
            content = msg[len('new'):].strip()
            
            # 2. Extract parts based on pipe delimiter
            parts = content.split('|')
            if len(parts) < 5:
                # If the user sends a simple 'new' or bad format, return the instructions
                resp.message(
                    "❌ *Input Error*: Missing details or incorrect format.\n"
                    "Example: `new 1. John Doe|2. Suit|3. Wool|4. 3m|5. 2025-12-15`"
                )
                return str(resp) # <-- FIXED!

            # Clean and extract data...
# ...

            # Clean and extract data (strips the number/period/space - e.g., '1. John Doe' -> 'John Doe')
            client_name = re.sub(r"^\s*\d+\.\s*", "", parts[0].strip(), count=1)
            garment_type = re.sub(r"^\s*\d+\.\s*", "", parts[1].strip(), count=1)
            fabric_type = re.sub(r"^\s*\d+\.\s*", "", parts[2].strip(), count=1)
            quantity_str = re.sub(r"^\s*\d+\.\s*", "", parts[3].strip(), count=1)
            job_out_date_str = re.sub(r"^\s*\d+\.\s*", "", parts[4].strip(), count=1)


            # A. Parse Quantity 
            match = re.match(r"(\d+(\.\d+)?)\s*([a-zA-Z]+)", quantity_str)
            if not match:
                 resp.message("❌ *Input Error*: Quantity needed must include a number and unit (e.g., '3m', '5.5 yards').")
                 return
                 
            required_qty = float(match.group(1))
            required_unit = match.group(3).lower().strip()
            
            # B. Material Check, Stock Update, and 'materials_needed' Calculation
            conn = get_db_connection()
            materials_to_buy = ""
            
            stock_item = conn.execute("SELECT quantity, unit, material FROM store WHERE material COLLATE NOCASE LIKE ?", ('%' + fabric_type + '%',)).fetchone()
            
            if stock_item and stock_item['unit'].lower() == required_unit:
                available_qty = stock_item['quantity']
                
                if available_qty >= required_qty:
                    materials_to_buy = "NONE"
                    new_stock_qty = available_qty - required_qty
                    conn.execute("UPDATE store SET quantity = ? WHERE material = ?", (new_stock_qty, stock_item['material']))
                    stock_status = "✅ Materials in stock."
                else:
                    deficit = required_qty - available_qty
                    materials_to_buy = f"{deficit:.1f} {required_unit} of {fabric_type}"
                    conn.execute("UPDATE store SET quantity = 0 WHERE material = ?", (stock_item['material'],))
                    stock_status = f"⚠️ **BUY:** {materials_to_buy}"

            else:
                materials_to_buy = f"{required_qty:.1f} {required_unit} of {fabric_type}"
                stock_status = f"⚠️ **BUY:** {materials_to_buy}"

            # C. Time Estimation 
            estimated_time = get_time_estimate(required_qty)
            
            # D. Insert New Order
            result = conn.execute(
                """
                INSERT INTO orders (client_name, garment_type, fabric_type, job_out_date, status, materials_needed, job_in_date) 
                VALUES (?, ?, ?, ?, 'PENDING', ?, ?)
                """,
                (client_name, garment_type, fabric_type, job_out_date_str, materials_to_buy, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            )
            order_id = result.lastrowid
            conn.commit()
            conn.close()

            # E. Generate Receipt/Feedback
            receipt_msg = f"🎉 *New Order Created! (ID #{order_id})*\n"
            receipt_msg += f"👤 *1. Client:* {client_name}\n"
            receipt_msg += f"👗 *2. Garment:* {garment_type} ({required_qty:.1f} {required_unit})\n"
            receipt_msg += f"🗓️ *5. Due Date:* {job_out_date_str}\n"
            receipt_msg += f"⏱️ *Time Estimate:* {estimated_time:.1f} hours\n"
            receipt_msg += f"📦 *Stock Check:* {stock_status}"
            
            resp.message(receipt_msg)

        except Exception as e:
            resp.message(
                "❌ *An unexpected error occurred during order creation.* Please re-read the format instructions:\n"
                "Example: `new 1. John Doe|2. Suit|3. Wool|4. 3m|5. 2025-12-15`"
    elif command in ['start', 'complete'] and role != 'GUEST': 
        try:
            order_id = int(msg.split()[1])
            new_status = 'IN PROGRESS' if command == 'start' else 'COMPLETE'
            
            conn = get_db_connection()
            conn.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
            conn.commit()
            conn.close()

            if new_status == 'COMPLETE':
                resp.message(f"✂️ *Order #{order_id} complete!* Great work.")
            else:
                resp.message(f"🧵 *Order #{order_id} is now IN PROGRESS.*")

        except (IndexError, ValueError):
            resp.message(f"❌ *Command Error*: Please specify the Order ID. E.g., `{command} 101`")
        except sqlite3.Error:
            resp.message("❌ *Database Error*: Could not find or update the order.")
        except (IndexError, ValueError):
            resp.message(f"❌ *Command Error*: Please specify the Order ID. E.g., `{command} 101`")
        except sqlite3.Error:
            resp.message("❌ *Database Error*: Could not find or update the order.")


    # --- MANAGER COMMAND: prioritize ---
    elif command == 'prioritize' and role == 'MANAGER':
        try:
            client_name_query = msg[len('prioritize'):].strip()
            conn = get_db_connection()
            
            if client_name_query:
                # 1. Prioritize by client name
                orders = conn.execute("SELECT id, client_name, status, job_out_date FROM orders WHERE client_name LIKE ? AND status NOT IN ('COMPLETE', 'COLLECTED')", ('%' + client_name_query + '%',)).fetchall()

                if orders:
                    order_ids = [str(o['id']) for o in orders]
                    conn.execute(f"UPDATE orders SET priority_score = 1, status = 'PRIORITIZED' WHERE id IN ({','.join(order_ids)})")
                    conn.commit()
                    resp.message(f"🔥 *PRIORITY ALERT!* Orders {', '.join(order_ids)} for '{client_name_query}' have been set to PRIORITIZED.")
                else:
                    resp.message(f"❌ *Prioritize Error*: No active orders found for '{client_name_query}'.")

            else:
                # 2. List URGENT/OVERDUE orders if no client name is provided
                today_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                
                missed_deadline_orders = conn.execute("""
                    SELECT id, client_name, job_out_date, garment_type FROM orders 
                    WHERE job_out_date < ? AND status NOT IN ('COMPLETE', 'COLLECTED')
                    ORDER BY job_out_date ASC
                """, (today_date,)).fetchall()

                uncollected_orders = conn.execute("""
                    SELECT id, client_name, job_out_date, garment_type FROM orders 
                    WHERE status = 'COMPLETE'
                    ORDER BY job_out_date ASC
                """).fetchall()

                response_msg = "⚠️ *PRIORITY LIST: NO CLIENT NAME PROVIDED.*\n\n"
                
                if missed_deadline_orders:
                    response_msg += "🛑 *OVERDUE - MISSED DEADLINE* 🛑\n(Orders not complete, deadline passed)\n"
                    for order in missed_deadline_orders:
                        response_msg += f"  - *ID #{order['id']}* ({order['client_name']}) - DUE: {order['job_out_date']}\n"
                    response_msg += "\n"

                if uncollected_orders:
                    response_msg += "📦 *READY/UNCOLLECTED* 📦\n(Orders complete, awaiting pickup)\n"
                    for order in uncollected_orders:
                        response_msg += f"  - *ID #{order['id']}* ({order['client_name']}) - Garment: {order['garment_type']}\n"
                    response_msg += "\n"

                if not missed_deadline_orders and not uncollected_orders:
                    response_msg = "✅ *NO URGENT OR OVERDUE ORDERS.* The pipeline is clear."

                resp.message(response_msg)
            
            conn.close()
            
        except Exception as e:
            resp.message(f"❌ *An unexpected error occurred during prioritization*: {e}")


    # --- MANAGER/SALES GUY COMMAND: collected ---
    elif command == 'collected' and role in ['SALES_GUY', 'MANAGER']:
        try:
            order_id = int(msg.split()[1])
            conn = get_db_connection()
            conn.execute("UPDATE orders SET status = 'COLLECTED' WHERE id = ?", (order_id,))
            conn.commit()
            conn.close()

            resp.message(f"💵 *Order #{order_id} marked as COLLECTED.* Transaction complete. 🎉")

        except (IndexError, ValueError):
            resp.message("❌ *Command Error*: Please specify the Order ID. E.g., `collected 101`")
        except sqlite3.Error:
            resp.message("❌ *Database Error*: Could not find or update the order.")
            
            
    # --- STORE SEARCH: stock ---
    elif command == 'stock':
        material_query = msg[len('stock'):].strip()
        conn = get_db_connection()
        
        if not material_query:
            stock_items = conn.execute("SELECT material, quantity, unit FROM store ORDER BY material").fetchall()
            if stock_items:
                stock_list = "📦 *Current Store Inventory:*\n"
                for item in stock_items:
                    stock_list += f"- *{item['material']}*: {item['quantity']:.1f} {item['unit']}\n"
                resp.message(stock_list)
            else:
                resp.message("The store inventory is empty. Use `addstock` to add material.")
        else:
            item = conn.execute("SELECT material, quantity, unit FROM store WHERE material COLLATE NOCASE LIKE ?", ('%' + material_query + '%',)).fetchone()
            if item:
                resp.message(f"✅ *Stock found:* {item['material']} has **{item['quantity']:.1f} {item['unit']}** left.")
            else:
                resp.message(f"❌ *Material not found:* No stock matching '{material_query}'.")

        conn.close()
        
    
    # --- ADD STOCK ---
    elif command == 'addstock' and role in ['MANAGER', 'SALES_GUY']:
        try:
            parts = msg[len('addstock'):].strip().split('|')
            if len(parts) < 3:
                resp.message("❌ *Command Error*: Invalid format. Use: `addstock [Material] | [Quantity] | [Unit]`")
                return

            material = parts[0].strip()
            quantity = float(parts[1].strip())
            unit = parts[2].strip()

            if quantity <= 0:
                resp.message("❌ *Stock Error*: Quantity must be a positive number.")
                return

            conn = get_db_connection()
            
            conn.execute(
                """
                INSERT INTO store (material, quantity, unit) 
                VALUES (?, ?, ?)
                ON CONFLICT(material) DO UPDATE SET 
                    quantity = quantity + excluded.quantity
                """,
                (material, quantity, unit)
            )
            conn.commit()
            
            current_stock = conn.execute("SELECT quantity, unit FROM store WHERE material = ?", (material,)).fetchone()
            conn.close()

            resp.message(
                f"✅ *Stock Updated!* Added {quantity:.1f} {unit} of **{material}**.\n"
                f"📦 New Total: **{current_stock['quantity']:.1f} {current_stock['unit']}**."
            )

        except ValueError:
            resp.message("❌ *Input Error*: Quantity must be a number.")
        except Exception as e:
            resp.message("❌ *An unexpected error occurred during stock update.*")

    
    # --- ALL ROLES COMMAND: pending ---
    elif command == 'pending':
        conn = get_db_connection()
        pending_orders = conn.execute("SELECT id, client_name, garment_type, materials_needed, status, priority_score FROM orders WHERE status NOT IN ('COMPLETE', 'COLLECTED') ORDER BY priority_score DESC, job_in_date ASC").fetchall()
        conn.close()
        
        if pending_orders:
            response_msg = "📋 *ACTIVE / PENDING JOBS:*\n\n"
            for order in pending_orders:
                status_icon = "🔥" if order['status'] == 'PRIORITIZED' else "⏳" if order['status'] == 'PENDING' else "🧵"
                
                material_status = "✅ Ready"
                if order['materials_needed'] != 'NONE':
                    material_status = f"⚠️ BUY: {order['materials_needed']}"
                    
                response_msg += (
                    f"{status_icon} *ID #{order['id']}* ({order['client_name']})\n"
                    f"  - Status: {order['status']} | Garment: {order['garment_type']}\n"
                    f"  - Materials: {material_status}\n\n"
                )
        else:
            response_msg = "🎉 *No active orders!* Everything is complete or collected."
            
        resp.message(response_msg)


    # --- DYNAMIC QUERY TOOL: query ---
    elif command == 'query':
        DB_COLUMNS = ['id', 'client_name', 'garment_type', 'size', 'color', 'fabric_type', 'job_out_date', 'status', 'materials_needed']
        COLUMN_MAP = {str(i+1): col for i, col in enumerate(DB_COLUMNS)}
        
        query_parts = msg[len('query'):].strip().split('|')

        if len(query_parts) == 1 and not query_parts[0]:
            column_list = "🔢 *Dynamic Query Tool*\n"
            column_list += "Select columns (e.g., `query 1,2,5`):\n"
            for num, col in COLUMN_MAP.items():
                column_list += f"- *{num}*: {col.replace('_', ' ').title()}\n"
            column_list += "\nOr use a filter: `query 1,2 | status=PENDING`"
            resp.message(column_list)
        
        else:
            try:
                col_selection = query_parts[0].strip().split(',')
                selected_cols = [COLUMN_MAP[num.strip()] for num in col_selection if num.strip() in COLUMN_MAP]
                
                if not selected_cols:
                    resp.message("❌ *Query Error*: Invalid column numbers selected.")
                    return

                select_clause = ", ".join(selected_cols)
                where_clause = ""
                where_params = []
                
                if len(query_parts) > 1 and query_parts[1].strip():
                    filter_str = query_parts[1].strip()
                    conditions = filter_str.split(' AND ')
                    sql_conditions = []
                    
                    for condition in conditions:
                        if '=' in condition:
                            key, value = condition.split('=', 1)
                            key = key.strip().lower().replace(' ', '_')
                            value = value.strip()
                            
                            if key in DB_COLUMNS:
                                sql_conditions.append(f"{key} LIKE ?")
                                where_params.append('%' + value + '%')

                    if sql_conditions:
                        where_clause = " WHERE " + " AND ".join(sql_conditions)

                conn = get_db_connection()
                query = f"SELECT {select_clause} FROM orders{where_clause} ORDER BY id DESC LIMIT 10"
                results = conn.execute(query, where_params).fetchall()
                conn.close()

                if results:
                    header = " | ".join([col.replace('_', ' ').title() for col in selected_cols])
                    output = f"🔎 *Query Results (Top {len(results)}):*\n{header}\n"
                    output += "-" * len(header) + "\n"
                    for row in results:
                        row_str = " | ".join(str(row[col]) for col in selected_cols)
                        output += row_str + "\n"
                    resp.message(output)
                else:
                    resp.message("No orders found matching your criteria.")

            except Exception as e:
    else:
        # 1. Define Role Header
        if role == 'MANAGER':
            header = "👋 *Welcome, Manager!* Select an option (e.g., send *1*):"
        elif role in ['TAILOR_1', 'TAILOR_2']:
            header = f"🧵 *Welcome, Tailor ({role.split('_')[1]})!* Select your next action:"
        elif role == 'SALES_GUY':
            header = "👔 *Welcome, Sales Guy!* Select an option below:"
        else:
            header = "Hello! Choose an option by number:"
            
        help_message = f"{header}\n\n*General Functions (All Roles):*\n"
        
        # General Commands (Accessible to ALL)
        help_message += "1. **➕ Create New Order** (Enter order details)\n"
        help_message += "2. **📋 View Pending Jobs**\n"
        help_message += "3. **📦 Check Store Stock**\n"
        help_message += "4. **🔎 Run Database Query**\n"
        
        # Role-Specific Commands
        if role in ['TAILOR_1', 'TAILOR_2']:
            help_message += "\n*Tailor Actions:*\n"
            help_message += "5. **▶️ Start Job** (Change status to 'IN PROGRESS')\n"
            help_message += "6. **✅ Complete Job** (Change status to 'COMPLETE')\n"

        if role == 'MANAGER':
            help_message += "\n*Manager Actions:*\n"
            help_message += "5. **🔥 Prioritize Jobs** (Mark urgent or list overdue)\n"
            help_message += "6. **💰 Mark as Collected**\n"
            help_message += "7. **➕ Add Stock**\n"

        if role == 'SALES_GUY':
            help_message += "\n*Sales Guy Actions:*\n"
            help_message += "5. **💰 Mark as Collected**\n"
            help_message += "6. **➕ Add Stock**\n"

        resp.message(help_message)

    return str(resp)

        resp.message(help_message)

    return str(resp)


# --- 5. RUN APPLICATION ---

if __name__ == "__main__":
    # Initialize the database file when the app starts
    init_db() 
    # NOTE: In a production environment (like Render/Heroku), you would use a 
    # WSGI server (like Gunicorn) and ensure environment variables are set.
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
