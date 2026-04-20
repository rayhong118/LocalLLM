import database
db = database.SessionLocal()
contexts = db.query(database.Context).all()
with open('scratch/contexts.txt', 'w', encoding='utf-8') as f:
    for c in contexts:
        f.write(f"Name: {c.name}\nContent: {c.content}\n---\n")
db.close()
