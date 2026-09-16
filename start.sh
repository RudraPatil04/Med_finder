#!/bin/bash
############################################################
# MED FINDER - START APPLICATION
# Version : 1.0
############################################################

echo ""
echo "============================================================"
echo "MED FINDER - STARTING APPLICATION"
echo "============================================================"
echo ""

# Check if database exists
if [ ! -f "database/medfinder.db" ]; then
    echo "[WARNING] Database not found!"
    echo "Please run the ETL pipeline first: python run_etl.py"
    echo ""
    exit 1
fi

# Check if source column exists in database (for API fallback)
echo "[*] Checking database schema..."
python -c "import sqlite3; conn=sqlite3.connect('database/medfinder.db'); cursor=conn.cursor(); cursor.execute('PRAGMA table_info(medicines)'); cols=[c[1] for c in cursor.fetchall()]; print('[OK] Database ready' if 'source' in cols else '[WARNING] Run: python migrate_add_source.py'); conn.close()"

echo ""
echo "[*] Installing/updating dependencies..."
pip install -q -r requirements.txt

echo ""
echo "============================================================"
echo "STARTING FLASK SERVER"
echo "============================================================"
echo ""
echo "Application will be available at:"
echo ""
echo "    http://127.0.0.1:5000"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""
echo "============================================================"
echo ""

# Start Flask application
python app.py
