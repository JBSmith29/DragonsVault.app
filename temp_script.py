from app import create_app
from extensions import db
from models import User
app = create_app()
app.config['WTF_CSRF_ENABLED'] = False
ctx = app.app_context(); ctx.push()
try:
    db.create_all()
    user = User(email='test@example.com', username='tester')
    user.set_password('password')
    db.session.add(user)
    db.session.commit()
    client = app.test_client()
    client.post('/login', data={'identifier': 'test@example.com', 'password': 'password'}, follow_redirects=True)
    resp = client.post('/build-a-deck', data={'commander_name': "Atraxa, Praetors' Voice", 'deck_name': 'Test Deck', 'deck_tag': 'Aggro'}, follow_redirects=True)
    print('status', resp.status_code)
    print('contains', b'Test Deck' in resp.data)
finally:
    ctx.pop()
