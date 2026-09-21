import sys
import os
import statistics
import pandas as pd
import matplotlib.pyplot as plt
from datetime import date

from core.db import init_db, get_db_connection, export_to_csv_backup
from core.audit_engine import (
    lookup_product,
    get_stock_warnings,
    get_financial_summary,
    add_bill as engine_add_bill,
    add_receipt as engine_add_receipt,
    calculate_profit_prediction,
    add_product as engine_add_product,
    update_price as engine_update_price,
    remove_product as engine_remove_product
)

# Initialize database
init_db()


def display():
    conn = get_db_connection()
    df = pd.read_sql_query("SELECT product_id, product_name, product_price, product_amount_left, product_amount_sold, product_type FROM inventory", conn)
    conn.close()
    print("\n--- Current Inventory ---")
    print(df.to_string(index=False))


def budget():
    summary = get_financial_summary()
    print(f"\nCurrent Budget Balance: ${summary['current_balance']:.2f}")
    print(f"Net Profit/Loss:        ${summary['net_profit']:.2f}")


def profit_graph():
    pred = calculate_profit_prediction()
    if not pred["has_enough_data"]:
        print("Not enough balance history records to generate graph.")
        return

    dates = pred["dates"]
    profits = pred["actual_profits"]

    plt.figure(figsize=(9, 5))
    plt.plot(dates, profits, marker='o', color='green', linestyle='-', label='Net Profit')
    plt.plot(dates, pred["trend_line"], color='blue', linestyle='--', label='Linear Trend')
    plt.xlabel("Date")
    plt.ylabel("Net Profit / Loss ($)")
    plt.title("Budget Growth / Profit Over Time")
    plt.grid(True)
    plt.xticks(rotation=45)
    plt.legend()
    plt.tight_layout()
    plt.show()


def product_search():
    raw_input = input("Which product are you looking for? ").strip()
    if not raw_input:
        print("Input cannot be empty.")
        return

    prod = lookup_product(raw_input)
    if prod:
        print("\nItem found!")
        print(f" - Product ID: {prod['product_id']} | Name: {prod['product_name']} | Price: ${prod['product_price']:.2f} | Stock: {prod['product_amount_left']}")
    else:
        # Fallback to substring query
        conn = get_db_connection()
        rows = conn.execute("SELECT product_id, product_name, product_price, product_amount_left FROM inventory WHERE LOWER(product_name) LIKE ?;", (f"%{raw_input.lower()}%",)).fetchall()
        conn.close()
        if rows:
            print(f"\nFound {len(rows)} matching items:")
            for r in rows:
                print(f" - Product ID: {r['product_id']} | Name: {r['product_name']} | Price: ${r['product_price']:.2f} | Stock: {r['product_amount_left']}")
        else:
            print("Item not found.")


def statistical_approximation_of_profit():
    pred = calculate_profit_prediction()
    if not pred["has_enough_data"]:
        print("Not enough data to calculate statistical approximation.")
        return

    print("\n--- Statistical Profit Prediction ---")
    print(f"Trend Line Equation:    {pred['equation']}")
    print(f"Daily Run-Rate Slope:   ${pred['daily_slope']:.2f} / day")
    print(f"30-Day Estimated Profit: ${pred['prediction_30d']:.2f}")
    print(f"90-Day Estimated Profit: ${pred['prediction_90d']:.2f}")
    print(f"1-Year Estimated Profit: ${pred['prediction_1yr']:.2f}")

    # Plot regression chart
    plt.figure(figsize=(9, 5))
    plt.scatter(pred["dates"], pred["actual_profits"], color='green', label='Actual Profit', zorder=3)
    plt.plot(pred["dates"], pred["trend_line"], color='blue', linestyle='--', label='Linear Trend', zorder=2)
    plt.xlabel("Date")
    plt.ylabel("Net Profit / Loss ($)")
    plt.title("Statistical Approximation of Profit (1-Year Projection)")
    plt.xticks(rotation=45)
    plt.legend()
    plt.grid(True, zorder=1)
    plt.tight_layout()
    plt.show()


def warning_amount_need_refill(threshold=50):
    warnings = get_stock_warnings(threshold=threshold)
    if warnings:
        print("\n⚠️  WARNING: The following products need a refill:")
        for r in warnings:
            sev = f"[{r['severity']}]"
            print(f" {sev} {r['product_name']} (ID: {r['product_id']}): Only {r['product_amount_left']} units left!")
    else:
        print("\n✅ All stock levels are looking good!")


def add_bill(amount, reason):
    new_bal = engine_add_bill(amount, reason)
    print(f"Successfully added bill: ${amount:.2f} for '{reason}'. New balance: ${new_bal:.2f}")


def add_receipt(amount, reason):
    new_bal = engine_add_receipt(amount, reason)
    print(f"Successfully added receipt: ${amount:.2f} from '{reason}'. New balance: ${new_bal:.2f}")


def show_history_stats():
    summary = get_financial_summary()
    print("\n--- Financial Summary Statistics ---")
    print(f"Current Balance:        ${summary['current_balance']:.2f}")
    print(f"Initial Starting Base:  ${summary['initial_budget']:.2f}")
    print(f"Total Money In:         ${summary['total_money_in']:.2f}")
    print(f"Total Money Out:        ${summary['total_money_out']:.2f}")
    print(f"Net Profit / Loss:      ${summary['net_profit']:.2f}")
    print(f"Total Sales Revenue:    ${summary['sales_revenue']:.2f} ({summary['units_sold']} items sold)")
    print(f"Inventory Valuation:    ${summary['inventory_valuation']:.2f}")


def add_product(prod_id, name, price, amount_left, amount_sold, date_filled, prod_type):
    engine_add_product(prod_id, name, price, amount_left, amount_sold, date_filled, prod_type)
    print(f"Successfully added product: {name} ({prod_id})")


def remove_product(prod_id):
    try:
        engine_remove_product(prod_id)
        print(f"Product ID {prod_id} successfully removed.")
    except Exception as e:
        print(f"Could not remove product: {e}")


def change_price(prod_id, new_price):
    try:
        engine_update_price(prod_id, new_price)
        print(f"Updated product {prod_id} price to ${new_price:.2f}")
    except Exception as e:
        print(f"Error updating price: {e}")


def print_menu():
    print("\n==============================")
    print("      AUDITING SYSTEM MENU    ")
    print("==============================")
    print("1. Display Inventory")
    print("2. Check Current Budget")
    print("3. Search Product")
    print("4. View Profit Graph")
    print("5. 1-Year Profit Prediction")
    print("6. Check Stock Warnings")
    print("7. Add Bill (Expense)")
    print("8. Add Receipt (Income)")
    print("9. Show Financial Summary Statistics")
    print("10. Add New Product")
    print("11. Remove Product")
    print("12. Change Product Price")
    print("Type 'q', 'quit', 'exit', or 'stop' to quit.")


def main(option):
    if option == 1:
        display()
    elif option == 2:
        budget()
    elif option == 3:
        product_search()
    elif option == 4:
        profit_graph()
    elif option == 5:
        statistical_approximation_of_profit()
    elif option == 6:
        warning_amount_need_refill()
    elif option == 7:
        try:
            amt = float(input("Enter bill amount: "))
            reason = input("Enter reason for bill: ")
            add_bill(amt, reason)
        except ValueError:
            print("❌ Invalid amount entered. Please use numbers.")
    elif option == 8:
        try:
            amt = float(input("Enter receipt amount: "))
            reason = input("Enter source/reason: ")
            add_receipt(amt, reason)
        except ValueError:
            print("❌ Invalid amount entered. Please use numbers.")
    elif option == 9:
        show_history_stats()
    elif option == 10:
        pid = input("Enter Product ID: ")
        name = input("Enter Product Name: ")
        try:
            price = float(input("Enter Product Price: "))
            left = int(input("Enter Amount Left: "))
            sold = int(input("Enter Amount Sold: "))
        except ValueError:
            print("❌ Invalid numeric value entered for price or amount.")
            return
        filled = input("Enter Date Filled (YYYY-MM-DD): ")
        ptype = input("Enter Product Type: ")
        add_product(pid, name, price, left, sold, filled, ptype)
    elif option == 11:
        pid = input("Enter Product ID to remove: ")
        remove_product(pid)
    elif option == 12:
        pid = input("Enter Product ID to update: ")
        try:
            new_p = float(input("Enter new price: "))
            change_price(pid, new_p)
        except ValueError:
            print("❌ Invalid price entered.")
    else:
        print("❌ Option not recognized. Please choose a number between 1 and 12.")


if __name__ == "__main__":
    print("Welcome to Supermarket Auditing System (Enterprise Engine)!")
    warning_amount_need_refill()

    while True:
        print_menu()
        user_input = input("\nEnter an option number: ").strip().lower()

        if user_input in ["q", "quit", "exit", "stop"]:
            print("Exiting auditing system. Goodbye!")
            sys.exit()

        try:
            option_num = int(user_input)
            main(option_num)
        except ValueError:
            print("❌ Invalid input! Please enter a valid menu number or type 'quit'.")