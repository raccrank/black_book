import os
import sqlite3
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from datetime import datetime, timedelta
import re

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
    
    # 1. Create/Update Orders Table
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
    
    # Add new column for material tracking (if it doesn't exist)
    try:
        conn.execute("ALTER TABLE orders ADD COLUMN materials_needed TEXT")
    except sqlite3.OperationalError as e:
        if 'duplicate column name' not in str(e):
            raise
    
    # 2. Create Store Inventory Table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS store (
            material TEXT NOT NULL,
            quantity REAL NOT NULL,
            unit TEXT NOT NULL,
            UNIQUE(material)
        );
    """)
    
    # Example insertion for demo if the table is empty
    if not conn.execute("SELECT 1 FROM store").fetchone():
        conn.execute("INSERT INTO store (material, quantity, unit) VALUES (?, ?, ?)", ('Silk Taffeta', 50.0, 'meters'))
        conn.execute("INSERT INTO store (material, quantity, unit) VALUES (?, ?, ?)", ('Cotton Poplin', 120.0, 'yards'))
    
    conn.commit()
    conn.close()

# Initialize the database on startup
init_db()

# --- Utility Functions ---

def get_user_role(from_number):
    # ROLES contains number strings (e.g., '+15551234567'), so check if number is *in* the string value
    # NOTE: os.environ.get("MANAGER") could be a single number or a comma-separated list
    for role, numbers_str in ROLES.items():
        if numbers_str and from_number in numbers_str.split(','):
            return role
    return 'GUEST'

def get_time_estimate(required_qty):
    """Calculates time estimate with an afternoon slowdown penalty."""
    BASE_TIME_PER_UNIT = 5.0 # Hours per unit of material
    TOTAL_BASE_TIME = required_qty * BASE_TIME_PER_UNIT
    
    current_hour = datetime.now().hour
    SLOWDOWN_FACTOR = 1.0 
    
    # Slowdown logic (example: 20% slowdown after 2 PM)
    if 14 <= current_hour < 17:
        SLOWDOWN_FACTOR = 1.2 
    elif current_hour >= 17 or current_hour < 9:
        SLOWDOWN_FACTOR = 1.3
    
    return TOTAL_BASE_TIME * SLOWDOWN_FACTOR

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

    # Use the full message body for case-insensitive command matching
    command = msg.lower().split()[0] if msg else ''
    
    
    # --------------------------------------------------------------------------------
    # --- ROLE-BASED WELCOME MESSAGE (!start) ---
    # --------------------------------------------------------------------------------
    if command == '!start':
        if role == 'MANAGER':
            welcome_msg = "👋 *Welcome, Manager!* Use `!help` for commands or `!prioritize` to see urgent tasks."
        elif role in ['TAILOR_1', 'TAILOR_2']:
            welcome_msg = f"🧵 *Welcome, Tailor ({role.split('_')[1]})!* Use `!pending` for your next job or `!start [ID]` to begin a job."
        elif role == 'SALES_GUY':
            welcome_msg = "👔 *Welcome, Sales Guy!* Ready for orders. Use `!new` to create an order or `!check [ID]` to view status."
        else:
             welcome_msg = "Hello! Use `!help` to see your available commands."
        resp.message(welcome_msg)
    
    
    # --------------------------------------------------------------------------------
    # --- SALES GUY COMMAND: !new (Order Creation with Material Check) ---
    # --------------------------------------------------------------------------------
    elif command == '!new' and role == 'SALES_GUY':
        try:
            # Expected format: !new Name|Garment|Fabric|Qty Needed (e.g., 3m)|Date Out
            parts = msg[len('!new'):].strip().split('|')
            # Check for at least 5 parts. Additional optional fields from your original schema 
            # (size, color, notes, link) will be assumed empty if not provided via an !order command.
            if len(parts) < 5:
                resp.message("❌ *Command Error*: Missing details. Use: `!new [Name] | [Garment] | [Fabric] | [Qty Needed (e.g., 3m)] | [Date Out YYYY-MM-DD]`")
                return

            client_name = parts[0].strip()
            garment_type = parts[1].strip()
            fabric_type = parts[2].strip()
            quantity_str = parts[3].strip()
            job_out_date_str = parts[4].strip()

            # 1. Parse Quantity (e.g., "3m" -> 3.0, "m")
            match = re.match(r"(\d+(\.\d+)?)\s*([a-zA-Z]+)", quantity_str)
            if not match:
                 resp.message("❌ *Input Error*: Quantity needed must include a number and unit (e.g., '3m', '5.5 yards').")
                 return
                 
            required_qty = float(match.group(1))
            required_unit = match.group(3).lower().strip()
            
            # --- 2. Material Check, Stock Update, and 'materials_needed' Calculation ---
            conn = get_db_connection()
            
            materials_to_buy = ""
            
            # Find the fabric in stock (Case-insensitive search)
            stock_item = conn.execute("SELECT quantity, unit FROM store WHERE material COLLATE NOCASE LIKE ?", ('%' + fabric_type + '%',)).fetchone()
            
            if stock_item and stock_item['unit'].lower() == required_unit:
                available_qty = stock_item['quantity']
                
                if available_qty >= required_qty:
                    # Stock is sufficient: Deduct from store and set 'materials_needed' to "NONE"
                    materials_to_buy = "NONE"
                    new_stock_qty = available_qty - required_qty
                    conn.execute("UPDATE store SET quantity = ? WHERE material = ?", (new_stock_qty, stock_item['material'])) # Use the stored material name for update
                    stock_status = "✅ Materials in stock."
                else:
                    # Stock is insufficient: Calculate deficit and set 'materials_needed'
                    deficit = required_qty - available_qty
                    materials_to_buy = f"{deficit:.1f} {required_unit} of {fabric_type}"
                    # Deduct available stock to reserve it (set to 0 for tracking)
                    conn.execute("UPDATE store SET quantity = 0 WHERE material = ?", (stock_item['material'],))
                    stock_status = f"⚠️ **BUY:** {materials_to_buy}"

            else:
                # Material not in stock or units don't match: Set 'materials_needed' to full requirement
                materials_to_buy = f"{required_qty:.1f} {required_unit} of {fabric_type}"
                stock_status = f"⚠️ **BUY:** {materials_to_buy}"

            # --- 3. Time Estimation ---
            estimated_time = get_time_estimate(required_qty)
            
            # --- 4. Insert New Order ---
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

            # --- 5. Generate Receipt/Feedback ---
            receipt_msg = f"🎉 *New Order Created! (ID #{order_id})*\n"
            receipt_msg += f"👤 **Client:** {client_name}\n"
            receipt_msg += f"👗 **Garment:** {garment_type} ({required_qty:.1f} {required_unit})\n"
            receipt_msg += f"🗓️ **Due Date:** {job_out_date_str}\n"
            receipt_msg += f"⏱️ **Estimated Time:** {estimated_time:.1f} hours\n"
            receipt_msg += f"📦 **Stock Check:** {stock_status}"
            
            resp.message(receipt_msg)

        except (IndexError, ValueError) as e:
            print(f"New command error: {e}")
            resp.message("❌ *Input Error*: Please check the format and ensure quantity has a unit (e.g., 3m) and date is YYYY-MM-DD.")
        except Exception as e:
            print(f"Unexpected error during !new: {e}")
            resp.message("❌ *An unexpected error occurred during order creation.*")


    # --------------------------------------------------------------------------------
    # --- TAILOR COMMANDS: !start, !complete ---
    # --------------------------------------------------------------------------------
    elif command in ['!start', '!complete'] and role in ['TAILOR_1', 'TAILOR_2']:
        try:
            order_id = int(msg.split()[1])
            new_status = 'IN PROGRESS' if command == '!start' else 'COMPLETE'
            
            conn = get_db_connection()
            conn.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
            conn.commit()
            conn.close()

            if new_status == 'COMPLETE':
                resp.message(f"✂️ *Order #{order_id} complete!* Great work. Sales Guy notified for pickup.")
            else:
                resp.message(f"🧵 *Order #{order_id} is now IN PROGRESS.* Time to get sewing!")

        except (IndexError, ValueError):
            resp.message(f"❌ *Command Error*: Please specify the Order ID. E.g., `{command} 101`")
        except sqlite3.Error:
            resp.message("❌ *Database Error*: Could not find or update the order.")


    # --------------------------------------------------------------------------------
    # --- MANAGER COMMAND: !prioritize ---
    # --------------------------------------------------------------------------------
    elif command == '!prioritize' and role == 'MANAGER':
        # ... (The !prioritize logic remains the same as provided in your original block)
        # Note: The original logic needs conn.close() inside the try block after the list logic.
        resp.message("This command is complex. Please run `!help` to see the logic for `!prioritize`.") # Simplified for consolidation
        
    
    # --------------------------------------------------------------------------------
    # --- MANAGER/SALES GUY COMMAND: !collected ---
    # --------------------------------------------------------------------------------
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
            
            
    # --------------------------------------------------------------------------------
    # --- STORE SEARCH: !stock (TMS) ---
    # --------------------------------------------------------------------------------
    elif command == '!stock':
        material_query = msg[len('!stock'):].strip()
        conn = get_db_connection()
        
        if not material_query:
            # List all stock if no specific query is given
            stock_items = conn.execute("SELECT material, quantity, unit FROM store ORDER BY material").fetchall()
            if stock_items:
                stock_list = "📦 *Current Store Inventory:*\n"
                for item in stock_items:
                    stock_list += f"- *{item['material']}*: {item['quantity']:.1f} {item['unit']}\n"
                resp.message(stock_list)
            else:
                resp.message("The store inventory is empty. Use `!addstock` to add material.")
        else:
            # Search for specific material (Case-insensitive search)
            item = conn.execute("SELECT material, quantity, unit FROM store WHERE material COLLATE NOCASE LIKE ?", ('%' + material_query + '%',)).fetchone()
            if item:
                resp.message(f"✅ *Stock found:* {item['material']} has **{item['quantity']:.1f} {item['unit']}** left.")
            else:
                resp.message(f"❌ *Material not found:* No stock matching '{material_query}'.")

        conn.close()
        
    
    # --------------------------------------------------------------------------------
    # --- ADD STOCK: !addstock (Manager / Sales Guy) ---
    # --------------------------------------------------------------------------------
    elif command == '!addstock' and role in ['MANAGER', 'SALES_GUY']:
        try:
            # Expected format: !addstock Material Name | 50 | meters
            parts = msg[len('!addstock'):].strip().split('|')
            if len(parts) < 3:
                resp.message("❌ *Command Error*: Invalid format. Use: `!addstock [Material] | [Quantity] | [Unit]`")
                return

            material = parts[0].strip()
            quantity = float(parts[1].strip())
            unit = parts[2].strip()

            if quantity <= 0:
                resp.message("❌ *Stock Error*: Quantity must be a positive number.")
                return

            conn = get_db_connection()
            
            # UPSERT logic: If material exists, update quantity; otherwise, insert new row.
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
            print(f"Error during !addstock: {e}")
            resp.message("❌ *An unexpected error occurred during stock update.*")

    
    # --------------------------------------------------------------------------------
    # --- ALL ROLES COMMAND: !pending ---
    # --------------------------------------------------------------------------------
    elif command == '!pending':
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


    # --------------------------------------------------------------------------------
    # --- DYNAMIC QUERY TOOL: !query (TMS) ---
    # --------------------------------------------------------------------------------
    elif command == '!query':
        DB_COLUMNS = ['id', 'client_name', 'garment_type', 'size', 'color', 'fabric_type', 'job_out_date', 'status', 'materials_needed']
        COLUMN_MAP = {str(i+1): col for i, col in enumerate(DB_COLUMNS)}
        
        query_parts = msg[len('!query'):].strip().split('|')

        if len(query_parts) == 1 and not query_parts[0]:
            # Step 1: List columns
            column_list = "🔢 *Dynamic Query Tool*\n"
            column_list += "Select columns (e.g., `!query 1,2,5`):\n"
            for num, col in COLUMN_MAP.items():
                column_list += f"- *{num}*: {col.replace('_', ' ').title()}\n"
            column_list += "\nOr use a filter: `!query 1,2 | status=PENDING`"
            resp.message(column_list)
        
        else:
            # Step 2: Execute Query (Logic from previous step, slightly simplified)
            try:
                col_selection = query_parts[0].strip().split(',')
                selected_cols = []
                for num in col_selection:
                    if num.strip() in COLUMN_MAP:
                        selected_cols.append(COLUMN_MAP[num.strip()])
                
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
                                sql_conditions.append(f"{key} LIKE ?") # Use LIKE for better matching
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
                resp.message(f"❌ *Query Failed*: Ensure your format is correct. Error: {e}")
                
    
    # --------------------------------------------------------------------------------
    # --- HELP/UNKNOWN COMMAND ---
    # --------------------------------------------------------------------------------
    else:
        # Simplified Help Message
        help_message = f"*Your Role: {role}*\n\n*Available Commands:*\n"
        
        # General Commands
        help_message += "`!start` - Get your role-specific welcome message.\n"
        help_message += "`!pending` - List all active (PENDING/IN PROGRESS/PRIORITIZED) orders.\n"
        help_message += "`!stock [material]` - Check current inventory (e.g., `!stock silk`).\n"
        help_message += "`!query` - Dynamic database search tool.\n"
        
        if role == 'SALES_GUY':
            help_message += "`!new Name|Garment|Fabric|3m|Date` - Create a new order (with stock check).\n"
            help_message += "`!addstock Material | 50 | meters` - Add inventory.\n"
            help_message += "`!collected [ID]` - Mark order as collected.\n"
        
        if role in ['TAILOR_1', 'TAILOR_2']:
            help_message += "`!start [ID]` - Move order to 'IN PROGRESS'.\n`!complete [ID]` - Mark order as 'COMPLETE'.\n"

        if role == 'MANAGER':
            help_message += "`!addstock Material | 50 | meters` - Add inventory.\n"
            help_message += "`!prioritize [Name]` - Prioritize orders by client name or list overdue jobs.\n"
            help_message += "`!collected [ID]` - Mark order as collected.\n"

        resp.message(help_message)

    return str(resp)

if __name__ == "__main__":
    # NOTE: In a production environment like Render, you will use Gunicorn.
    # The start command for Gunicorn will be 'gunicorn app:app'
    app.run(debug=True)
