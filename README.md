# Supermarket POS & Financial Audit System

An enterprise-grade, high-throughput, fault-tolerant Supermarket Point of Sale (POS) and Financial Audit web application built with Python, Flask, SQLite WAL (Write-Ahead Logging), and Waitress WSGI.

---

## Key Features

### 1. Cashier Point of Sale (POS)
- **Continuous Laser Barcode Scanning**: Auto-focus lock ensures barcode scanners scan repeatedly without requiring mouse clicks.
- **Audio Feedback**: Synthesized audio beep on scan via browser native Web Audio API (zero audio assets required).
- **Interactive Multi-Item Cart**: Real-time quantity adjustments, price calculation, and running totals.
- **Thermal Invoice / Receipt Printing**: Printable official tax receipt modal formatted for thermal and standard printers.
- **Bilingual Interface**: One-click toggle between **English** and **Arabic (العربية)** with automatic RTL/LTR page layout switching.

### 2. Executive Financial Audit & Intelligence
- **Real-Time KPI Metric Cards**: Live Current Balance, Net Profit, Units Sold, Inventory Valuation, and Stock Warning count.
- **Interactive Visual Analytics**: Dynamic profit progression curve with linear regression trend line powered by Chart.js.
- **Machine Learning Profit Forecasting**: Statistical 1-Year profit prediction using linear regression (`statistics.linear_regression`), computing daily run-rate slope, 30-day, 90-day, and 365-day trajectories.
- **Automated Stock Refill Alerts**: Immediate visual badges and warning tables for items at or below 50 units (critical at $\le$ 10 units).
- **Financial Ledger & Bookkeeping**: Instant recording of operational expense bills and revenue receipts with automated running balance updates.
- **Inventory Management**: Add products, edit prices with audit trail, delete products, and adjust stock.

### 3. Enterprise Security & Role-Based Access Control
- **Client-Side SHA-256 Password Hashing**: Passwords are cryptographically hashed in the browser using the native **Web Cryptography API (`crypto.subtle.digest`)** before transmission. Plaintext passwords never leave the user's device or travel across the network.
- **Role-Based Access Control (RBAC)**:
  - `@admin_required`: Restricts the **Admin Intelligence & Audit Center** strictly to users with the `admin` role.
  - `@login_required`: Protects POS operations, checkout APIs, and financial data from unauthenticated access.
- **Permission Barrier**: If a cashier attempts to access the Admin Panel, they are blocked by an animated **Access Denied** page instructing them to contact their system administrator for access.
- **User Account Management**: Administrators can create new user accounts, assign roles (`cashier` / `admin`), delete users, and manage access directly from the admin panel.

### 4. Interactive Live Health Monitoring Probe
- **Dual-Mode `/healthz`**:
  - **Browser View**: Sleek, dark-mode real-time telemetry dashboard with animated SVG semi-circular gauges, live CPU and RAM meters, disk space tracking, uptime clock (HH:MM:SS), live poll log, and a 5-second countdown auto-refresh.
  - **API / Probe View**: Machine-readable JSON response (`status: "UP"`, uptime, RSS memory, CPU%) for Kubernetes, Nginx, or AWS load balancers.
- **Readiness Probe (`/readyz`)**: Validates active SQLite connection and cache readiness.

### 5. High-Concurrency Architecture (Never Breaks)
- **ACID-Compliant SQLite WAL Engine**: Configured with `PRAGMA journal_mode = WAL`, `synchronous = NORMAL`, and `busy_timeout = 30000` for unlimited concurrent readers and serialized atomic writers with exponential backoff retries.
- **Dual-Sync CSV Compatibility**: Synchronizes active tables with `inventory.csv`, `sales.csv`, and `balance_history.csv` for external auditing.
- **L1 In-Memory LRU Cache**: Sub-millisecond (`<0.2ms`) barcode lookups.
- **Circuit Breaker & Error Boundary**: Prevents cascading failures and guarantees zero unhandled 500 crashes exposed to end users.

---

## Default Login Credentials

| Username | Password | Role | Permissions |
| :--- | :--- | :--- | :--- |
| **`admin`** | `admin123` | **Administrator** | Full access to POS, Admin Intelligence Hub, User Management, and Finance |
| **`john`** | `password1` | **Cashier** | POS Cashier operations & barcode checkout only *(Admin Panel blocked)* |
| **`sarah`** | `securePass99` | **Cashier** | POS Cashier operations & barcode checkout only *(Admin Panel blocked)* |

---

## Project Structure

```text
audit_system/
├── app.py                      # Flask application, routing, auth decorators & error boundary
├── audit.py                    # Interactive CLI auditing tool (synchronized with database)
├── run_production.py           # Multi-threaded Waitress WSGI production server
├── requirements.txt            # Python dependencies
├── Dockerfile                  # Container definition
├── docker-compose.yml          # Container orchestration
├── .gitignore                  # Git exclusions
├── inventory.csv               # Seed / backup inventory dataset
├── sales.csv                   # Seed / backup sales dataset
├── balance_history.csv         # Seed / backup balance history dataset
├── script.js                   # Client-side WebCrypto SHA-256 auth helper
├── core/
│   ├── __init__.py
│   ├── auth.py                 # SQLite user store, password verification & user management
│   ├── audit_engine.py         # POS checkout, linear regression, stock alerts & ledger
│   ├── cache_cb.py             # L1 LRU cache, circuit breaker & safe boundaries
│   └── db.py                   # SQLite WAL connection manager, retries & CSV seeding
├── templates/
│   ├── index.html              # Cashier POS interface (laser scan, cart, receipt, bilingual)
│   ├── admin.html              # Executive Admin & Audit Dashboard with Chart.js
│   ├── login.html              # Animated SHA-256 client-hashed sign-in page
│   ├── denied.html             # Role permission denied screen (contact admin)
│   └── health.html             # Live health monitor dashboard (SVG gauges, polling)
├── tests/
│   ├── __init__.py
│   └── test_audit_system.py    # Automated unit test suite (9 tests)
└── benchmark/
    └── stress_test.py          # Concurrency stress tester (500 parallel transactions)
```

---

## Getting Started

### Prerequisites
- Python 3.10 or higher

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/Naim-cspy/mini-market-audit-system.git
   cd mini-market-audit-system
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Start the production server:
   ```bash
   python run_production.py
   ```

4. Open your browser:
   - **Cashier POS**: [http://localhost:5000/](http://localhost:5000/)
   - **Admin & Audit Intelligence Center**: [http://localhost:5000/admin](http://localhost:5000/admin)
   - **Live Health Monitor**: [http://localhost:5000/healthz](http://localhost:5000/healthz)
   - **Sign In**: [http://localhost:5000/login](http://localhost:5000/login)

---

## Running with Docker

```bash
docker-compose up --build
```

---

## Automated Verification & Testing

### Run Unit Tests
```bash
python -m unittest tests/test_audit_system.py
```
*Executes 9 test suites covering database schema, CSV seeding, product lookup, atomic checkout, stock rollback, financial ledger, 1-year linear regression forecast, auth security/roles, and REST APIs.*

### Run High-Concurrency Stress Test
```bash
python benchmark/stress_test.py
```
*Simulates 25 concurrent virtual cashiers executing 500 parallel operations (barcode lookups and atomic checkouts) to verify 100% success rate with zero database locks.*

---

## License
MIT License
