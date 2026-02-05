import os
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_socketio import SocketIO, emit
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta, date
from sqlalchemy import func

app = Flask(__name__)
app.config['SECRET_KEY'] = 'khoa_bi_mat_cua_toi_123'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///cafe_final_v11.db' # Version 11 Final
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=60)

basedir = os.path.abspath(os.path.dirname(__file__))
app.config['UPLOAD_FOLDER'] = os.path.join(basedir, 'static', 'img')

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# --- MODELS (Không thay đổi cấu trúc, giữ nguyên để ổn định) ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    full_name = db.Column(db.String(100))
    
class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False)
    image = db.Column(db.String(500))
    category = db.Column(db.String(50))
    is_active = db.Column(db.Boolean, default=True)
    inventory = db.relationship('Inventory', backref='product', uselist=False, cascade="all, delete-orphan")

class Inventory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    stock_quantity = db.Column(db.Integer, default=100)

class DiscountCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    percentage = db.Column(db.Float, nullable=False)
    active = db.Column(db.Boolean, default=True)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    staff_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    status = db.Column(db.String(50), default='Pending')
    discount_percent = db.Column(db.Float, default=0)
    lines = db.relationship('OrderLine', backref='order', lazy=True, cascade="all, delete-orphan")
    bill = db.relationship('Bill', backref='order', uselist=False)

    @property
    def total_amount_before_discount(self): return sum(l.subtotal for l in self.lines)
    @property
    def final_total(self): return self.total_amount_before_discount * (1 - self.discount_percent / 100)
    @property
    def details_str(self): return ", ".join([f"{l.product_name} ({l.customization})" if l.customization else f"{l.product_name} x{l.quantity}" for l in self.lines])

class OrderLine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    product_name = db.Column(db.String(100))
    quantity = db.Column(db.Integer, nullable=False)
    price_at_time = db.Column(db.Float, nullable=False)
    customization = db.Column(db.String(200), default="")
    @property
    def subtotal(self): return self.quantity * self.price_at_time

class Bill(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    total_amount = db.Column(db.Float, nullable=False)
    discount_applied = db.Column(db.Float, default=0)
    final_amount = db.Column(db.Float, nullable=False)
    payment = db.relationship('Payment', backref='bill', uselist=False)

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bill_id = db.Column(db.Integer, db.ForeignKey('bill.id'), nullable=False)
    method = db.Column(db.String(50), default='QR Code')
    paid_at = db.Column(db.DateTime, default=datetime.now)

@login_manager.user_loader
def load_user(user_id): return User.query.get(int(user_id))

def init_db():
    if not os.path.exists(app.config['UPLOAD_FOLDER']): os.makedirs(app.config['UPLOAD_FOLDER'])
    with app.app_context():
        db.create_all()
        # Đảm bảo có Admin
        if not User.query.filter_by(username='admin').first():
            db.session.add(User(username='admin', password=generate_password_hash('123'), role='manager', full_name='Chủ Quán'))
        else:
            # Fix quyền admin nếu bị sai
            admin = User.query.filter_by(username='admin').first()
            if admin.role != 'manager': admin.role = 'manager'; admin.password = generate_password_hash('123')
        
        # Tạo Staff/Guest mẫu nếu chưa có
        if not User.query.filter_by(username='staff').first():
            db.session.add(User(username='staff', password=generate_password_hash('123'), role='staff', full_name='Nhân viên Mẫu'))
        if not User.query.filter_by(username='guest').first():
            db.session.add(User(username='guest', password=generate_password_hash('123'), role='customer', full_name='Khách'))
        
        if Product.query.count() == 0:
            p1 = Product(name="Cafe Đen", price=25000, category="Cafe", image="/static/img/cafe_den.jpg")
            p1.inventory = Inventory(stock_quantity=50)
            db.session.add(p1)
        db.session.commit()
init_db()

# --- ROUTES ---
@app.route('/')
def index():
    if not current_user.is_authenticated: return redirect(url_for('login'))
    if current_user.role == 'manager': return redirect(url_for('manager_dashboard'))
    elif current_user.role == 'staff': return redirect(url_for('staff_dashboard'))
    else: return redirect(url_for('customer_dashboard'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and check_password_hash(user.password, request.form.get('password')):
            login_user(user)
            if user.role == 'manager': return redirect(url_for('manager_dashboard'))
            elif user.role == 'staff': return redirect(url_for('staff_dashboard'))
            else: return redirect(url_for('customer_dashboard'))
        flash('Sai thông tin!', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout(): logout_user(); return redirect(url_for('login'))

@app.route('/customer')
@login_required
def customer_dashboard():
    # Use-case 1 (View Product) & 3 (Select Product)
    products = Product.query.join(Inventory).filter(Product.is_active == True, Inventory.stock_quantity > 0).all()
    return render_template('customer.html', products=products)

@app.route('/staff')
@login_required
def staff_dashboard():
    # Use-case 9 (Manage Bills) & 11 (Manage Sales)
    orders = Order.query.filter(Order.status != 'Completed').order_by(Order.created_at.desc()).all()
    products = Product.query.all()
    return render_template('staff.html', orders=orders, products=products)

@app.route('/manager', methods=['GET', 'POST'])
@login_required
def manager_dashboard():
    if current_user.role != 'manager': return "Access Denied"
    
    active_tab = request.args.get('tab', 'products') # Mặc định tab sản phẩm

    # Use-case 17 (View Revenue Report Period)
    today = date.today()
    rev_day = db.session.query(func.sum(Bill.final_amount)).filter(func.date(Bill.created_at) == today).scalar() or 0
    this_month = today.strftime('%Y-%m')
    rev_month = db.session.query(func.sum(Bill.final_amount)).filter(func.strftime('%Y-%m', Bill.created_at) == this_month).scalar() or 0
    start_week = today - timedelta(days=today.weekday())
    rev_week = db.session.query(func.sum(Bill.final_amount)).filter(func.date(Bill.created_at) >= start_week).scalar() or 0
    filtered_revenue = 0; report_title = ""
    
    if request.method == 'POST':
        # --- Use-case 10 & 13 (Manage Products) ---
        if 'save_product' in request.form:
            try:
                p_id = request.form.get('product_id')
                name = request.form['name']; price = float(request.form['price']); stock = int(request.form.get('stock', 100))
                category = request.form['category']; is_active = True if request.form.get('is_active') else False 
                image_path = request.form.get('image_url', '').strip()
                
                if not image_path and 'image_file' in request.files:
                    file = request.files['image_file']
                    if file.filename != '':
                        filename = secure_filename(file.filename)
                        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                        image_path = f"/static/img/{filename}"
                if not image_path:
                    if p_id: image_path = Product.query.get(p_id).image
                    else: image_path = "https://via.placeholder.com/150?text=No+Image"

                if p_id: # Update
                    prod = Product.query.get(p_id)
                    prod.name = name; prod.price = price; prod.category = category; 
                    prod.image = image_path; prod.is_active = is_active; prod.inventory.stock_quantity = stock
                    flash('Cập nhật món thành công!', 'info')
                else: # Add
                    new_prod = Product(name=name, price=price, category=category, image=image_path, is_active=is_active)
                    new_prod.inventory = Inventory(stock_quantity=stock)
                    db.session.add(new_prod)
                    flash('Thêm món thành công!', 'success')
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                flash(f"Lỗi khi lưu sản phẩm: {str(e)}", "danger")
            return redirect(url_for('manager_dashboard', tab='products'))
            
        elif 'delete_product' in request.form:
            Product.query.filter_by(id=request.form.get('product_id')).delete()
            db.session.commit()
            return redirect(url_for('manager_dashboard', tab='products'))

        # --- Use-case 18 (Employee Manage) FIX CRASH HERE ---
        elif 'save_employee' in request.form:
             try:
                 u_id = request.form.get('user_id')
                 uname = request.form['username']
                 fname = request.form['fullname']
                 pwd = request.form.get('password', '').strip()
                 
                 if u_id: # UPDATE NHÂN VIÊN (Use-case 18 Update)
                     user = User.query.get(u_id)
                     if user:
                         user.username = uname
                         user.full_name = fname
                         if pwd: user.password = generate_password_hash(pwd)
                         db.session.commit()
                         flash('Đã cập nhật thông tin nhân viên!', 'info')
                 else: # ADD NEW NHÂN VIÊN (Use-case 18 Add)
                     # Kiểm tra trùng tên đăng nhập
                     if User.query.filter_by(username=uname).first():
                         flash(f'Lỗi: Tên đăng nhập "{uname}" đã tồn tại!', 'danger')
                     else:
                         new_user = User(username=uname, password=generate_password_hash(pwd), full_name=fname, role='staff')
                         db.session.add(new_user)
                         db.session.commit()
                         flash(f'Đã tạo tài khoản cho nhân viên {fname}!', 'success')
             except Exception as e:
                 db.session.rollback()
                 flash(f"Lỗi hệ thống: {str(e)}", "danger")
                 print(f"Error saving employee: {e}")
             
             return redirect(url_for('manager_dashboard', tab='employees'))

        elif 'delete_employee' in request.form: # Use-case 18 Delete
            try:
                User.query.filter_by(id=request.form.get('user_id')).delete()
                db.session.commit()
                flash("Đã xóa nhân viên.", "success")
            except Exception as e:
                flash("Lỗi khi xóa nhân viên.", "danger")
            return redirect(url_for('manager_dashboard', tab='employees'))

        elif 'add_discount' in request.form:
             if not DiscountCode.query.filter_by(code=request.form['code'].upper()).first():
                 db.session.add(DiscountCode(code=request.form['code'].upper(), percentage=float(request.form['percent'])))
                 db.session.commit()
             return redirect(url_for('manager_dashboard', tab='discounts'))

        elif 'filter_report' in request.form:
            filter_type = request.form.get('filter_type'); date_val = request.form.get('date_val'); query = Bill.query
            if filter_type == 'day' and date_val: query = query.filter(func.date(Bill.created_at) == date_val)
            elif filter_type == 'month' and date_val: query = query.filter(func.strftime('%Y-%m', Bill.created_at) == date_val)
            elif filter_type == 'year' and date_val: query = query.filter(func.strftime('%Y', Bill.created_at) == date_val)
            bills = query.all()
            filtered_revenue = sum(b.final_amount for b in bills)

    products = Product.query.all()
    employees = User.query.filter_by(role='staff').all()
    discounts = DiscountCode.query.all()
    
    return render_template('manager.html', products=products, employees=employees, 
                           discounts=discounts, active_tab=active_tab,
                           rev_day=rev_day, rev_week=rev_week, rev_month=rev_month,
                           filtered_revenue=filtered_revenue, report_title=report_title)

# --- SOCKET EVENTS (Use-case 3, 4, 5, 6, 7, 8) ---
@socketio.on('check_discount_code')
def handle_check_discount(data):
    code_input = data.get('code', '').strip().upper()
    discount = DiscountCode.query.filter_by(code=code_input, active=True).first()
    if discount: emit('discount_result', {'valid': True, 'percent': discount.percentage, 'code': code_input}, room=request.sid)
    else: emit('discount_result', {'valid': False, 'msg': 'Mã không hợp lệ!'}, room=request.sid)

@socketio.on('new_order_request')
def handle_new_order(data):
    # Use-case 3, 4, 5 (Select, Quantity, Customize)
    try:
        discount_percent = data.get('discount_percent', 0)
        new_order = Order(customer_id=current_user.id, status='Pending', discount_percent=discount_percent)
        db.session.add(new_order); db.session.flush()
        items_details = []; total_price = 0
        for item in data['cart']:
            product = Product.query.get(item['id'])
            # Use-case 4.3: Check Stock
            if product and product.inventory.stock_quantity >= item['qty']:
                product.inventory.stock_quantity -= item['qty'] # Use-case 15 Auto Update
                cust_text = item.get('options', '')
                line = OrderLine(order_id=new_order.id, product_id=product.id, product_name=product.name, quantity=item['qty'], price_at_time=product.price, customization=cust_text)
                db.session.add(line); items_details.append(f"{product.name} x{item['qty']}"); total_price += (product.price * item['qty'])
        db.session.commit()
        final_total = total_price * (1 - discount_percent/100)
        emit('update_staff_orders', {'id': new_order.id, 'customer': current_user.full_name, 'details': ", ".join(items_details), 'total': final_total, 'time': new_order.created_at.strftime("%H:%M"), 'discount': discount_percent}, broadcast=True)
        emit('order_success_response', {'msg': 'Đã gửi đơn!'}, room=request.sid)
    except Exception as e: print("Lỗi:", e); db.session.rollback()

@socketio.on('staff_request_payment')
def handle_payment(data):
    # Use-case 7 (Issue Bill)
    order = Order.query.get(data['order_id'])
    if order:
        order.status = 'Paying'; raw_total = order.total_amount_before_discount
        discount_amount = raw_total * (order.discount_percent / 100)
        final_total = raw_total - discount_amount
        if not order.bill: db.session.add(Bill(order_id=order.id, total_amount=raw_total, discount_applied=discount_amount, final_amount=final_total))
        db.session.commit()
        bill_items = [{'name': l.product_name + (f" ({l.customization})" if l.customization else ""), 'qty': l.quantity, 'subtotal': l.subtotal} for l in order.lines]
        emit('show_customer_qr', {'total': final_total, 'raw_total': raw_total, 'discount': discount_amount, 'items': bill_items}, broadcast=True)

@socketio.on('staff_confirm_payment')
def handle_confirm(data):
    # Use-case 6 (Make Payment)
    order = Order.query.get(data['order_id'])
    if order and order.bill:
        order.status = 'Completed'; order.staff_id = current_user.id
        db.session.add(Payment(bill_id=order.bill.id, method='QR/Cash')); db.session.commit()
        emit('payment_success', {}, broadcast=True)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5001))
    socketio.run(app, host='0.0.0.0', port=port, debug=True, use_reloader=False, allow_unsafe_werkzeug=True)
