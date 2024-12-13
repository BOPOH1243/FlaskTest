from flask import Flask, jsonify, request, render_template, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import FlaskForm
from wtforms import StringField, FloatField, SelectField, SubmitField, IntegerField
from wtforms.validators import DataRequired
from flask_migrate import Migrate
from flask_admin import Admin
from flask_admin.contrib.sqla import ModelView
from flask.cli import with_appcontext
import click
from datetime import datetime, timedelta
from celery import Celery
import requests
from flasgger import Swagger
from flask_admin.base import BaseView, expose

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///test.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['CELERY_BROKER_URL'] = 'redis://localhost:6379/0'
app.config['CELERY_RESULT_BACKEND'] = 'redis://localhost:6379/0'

db = SQLAlchemy(app)
migrate = Migrate(app, db)
celery = Celery(app.name, broker=app.config['CELERY_BROKER_URL'])
celery.conf.update(app.config)
Swagger(app)

# Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    balance = db.Column(db.Float, default=0.0)
    commission_rate = db.Column(db.Float, default=0.01)
    webhook_url = db.Column(db.String(255), nullable=True)
    role = db.Column(db.String(50), default='user')  # 'admin' or 'user'
    usdt_wallet = db.Column(db.String(255), nullable=True)

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float, nullable=False)
    commission = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(50), default='pending')  # pending, confirmed, canceled, expired
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))

# Forms
class UserForm(FlaskForm):
    balance = FloatField('Balance', validators=[DataRequired()])
    commission_rate = FloatField('Commission Rate', validators=[DataRequired()])
    webhook_url = StringField('Webhook URL')
    role = SelectField('Role', choices=[('admin', 'Admin'), ('user', 'User')], validators=[DataRequired()])
    submit = SubmitField('Submit')

class TransactionForm(FlaskForm):
    amount = FloatField('Amount', validators=[DataRequired()])
    status = SelectField('Status', choices=[('pending', 'Pending'), ('confirmed', 'Confirmed'), ('canceled', 'Canceled'), ('expired', 'Expired')], validators=[DataRequired()])
    submit = SubmitField('Submit')

class DashboardView(BaseView):
    @expose('/')
    def index(self):
        users_count = User.query.count()
        transactions_count = Transaction.query.count()
        total_amount = db.session.query(db.func.sum(Transaction.amount)).filter(Transaction.created_at >= datetime.utcnow().date()).scalar() or 0
        recent_transactions = Transaction.query.order_by(Transaction.created_at.desc()).limit(10).all()
        return self.render('admin/dashboard.html',
                           users_count=users_count,
                           transactions_count=transactions_count,
                           total_amount=total_amount,
                           recent_transactions=recent_transactions)


# Admin
admin = Admin(app, template_mode='bootstrap3')
admin.add_view(ModelView(User, db.session))
admin.add_view(ModelView(Transaction, db.session))
admin.add_view(DashboardView(name='Dashboard', endpoint='dashboard'))

# CLI Command
@click.command('create-admin')
@with_appcontext
def create_admin():
    admin_user = User(balance=0.0, commission_rate=0.01, role='admin')
    db.session.add(admin_user)
    db.session.commit()
    click.echo('Admin user created.')

app.cli.add_command(create_admin)

# API Routes
@app.route('/api/create_transaction', methods=['POST'])
def create_transaction():
    """
    Create a transaction
    ---
    tags:
      - Transactions
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            user_id:
              type: integer
            amount:
              type: number
    responses:
      201:
        description: Transaction created
        schema:
          type: object
          properties:
            transaction_id:
              type: integer
      404:
        description: User not found
    """
    data = request.json
    user_id = data.get('user_id')
    amount = data.get('amount')
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    commission = amount * user.commission_rate
    transaction = Transaction(amount=amount, commission=commission, user_id=user_id)
    db.session.add(transaction)
    db.session.commit()
    return jsonify({'transaction_id': transaction.id}), 201

@app.route('/api/cancel_transaction', methods=['POST'])
def cancel_transaction():
    """
    Cancel a transaction
    ---
    tags:
      - Transactions
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            transaction_id:
              type: integer
    responses:
      200:
        description: Transaction status
        schema:
          type: object
          properties:
            status:
              type: string
      404:
        description: Transaction not found
    """
    data = request.json
    transaction_id = data.get('transaction_id')
    transaction = Transaction.query.get(transaction_id)
    if not transaction:
        return jsonify({'error': 'Transaction not found'}), 404

    if transaction.status == 'pending':
        transaction.status = 'canceled'
        db.session.commit()
    return jsonify({'status': transaction.status})

@app.route('/api/check_transaction', methods=['GET'])
def check_transaction():
    """
    Check a transaction
    ---
    tags:
      - Transactions
    parameters:
      - in: query
        name: transaction_id
        required: true
        type: integer
    responses:
      200:
        description: Transaction details
        schema:
          type: object
          properties:
            transaction_id:
              type: integer
            status:
              type: string
      404:
        description: Transaction not found
    """
    transaction_id = request.args.get('transaction_id')
    transaction = Transaction.query.get(transaction_id)
    if not transaction:
        return jsonify({'error': 'Transaction not found'}), 404

    return jsonify({
        'transaction_id': transaction.id,
        'status': transaction.status
    })

# Celery Task
@celery.task
def check_pending_transactions():
    now = datetime.datetime.utcnow()
    pending_transactions = Transaction.query.filter_by(status='pending').all()
    for transaction in pending_transactions:
        if (now - transaction.created_at).total_seconds() > 900:
            transaction.status = 'expired'
            db.session.commit()

            if transaction.user.webhook_url:
                requests.post(transaction.user.webhook_url, json={
                    'transaction_id': transaction.id,
                    'status': transaction.status
                })

if __name__ == '__main__':
    app.run(debug=True)
