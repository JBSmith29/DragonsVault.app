import sqlite3 
conn=sqlite3.connect('instance/database.db') 
cur=conn.cursor() 
cur.execute(\" SELECT name FROM sqlite_master WHERE "type=table\) 
print('tables',cur.fetchall()) 
cur.execute(\SELECT" id,name,category,commander_name FROM folder ORDER BY id DESC LIMIT "10\) 
print('folders',cur.fetchall()) 
conn.close()
