import os
from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_socketio import SocketIO, emit
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta

# --- CẤU HÌNH APP ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'khoa_bi_mat_cua_toi_123' # Key bảo mật
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///cafe_v2.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=60) 

# Cấu hình thư mục upload ảnh
basedir = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(basedir, 'static', 'img')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- KHỞI TẠO CÁC THÀNH PHẦN ---
db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Cấu hình SocketIO với eventlet để chạy mượt trên Render
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# --- MODELS (CẤU TRÚC DỮ LIỆU) ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False) # manager, staff, customer
    full_name = db.Column(db.String(100))
    orders_processed = db.relationship('Order', backref='processor', lazy=True)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False)
    image = db.Column(db.String(200)) 
    category = db.Column(db.String(50))

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_name = db.Column(db.String(100), default="Khách lẻ")
    total_amount = db.Column(db.Float, default=0)
    status = db.Column(db.String(50), default='Pending') # Pending, Paying, Completed
    created_at = db.Column(db.DateTime, default=datetime.now)
    details = db.Column(db.String(500))
    staff_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- HÀM TẠO DATABASE (QUAN TRỌNG) ---
def init_db():
    # Kiểm tra và tạo thư mục ảnh nếu chưa có
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)
        
    with app.app_context():
        db.create_all() # Tạo các bảng
        
        # Kiểm tra nếu chưa có Admin thì tạo mới
        if not User.query.filter_by(username='admin').first():
            print("--- ĐANG KHỞI TẠO DỮ LIỆU MẪU ---")
            # 1. Tạo Admin
            admin = User(username='admin', password=generate_password_hash('123'), role='manager', full_name='Chủ Quán')
            db.session.add(admin)
            
            # 2. Tạo Nhân viên
            staff = User(username='staff', password=generate_password_hash('123'), role='staff', full_name='Nhân viên A')
            db.session.add(staff)
            
            # 3. Tạo Khách hàng (dùng chung)
            guest = User(username='guest', password=generate_password_hash('123'), role='customer', full_name='Khách hàng')
            db.session.add(guest)
            
            db.session.commit()
            print("--- ĐÃ TẠO XONG TÀI KHOẢN: admin/123 ---")

# --- GỌI HÀM TẠO DB NGAY LẬP TỨC ---
# Dòng này giúp Render tự chạy DB mà không cần đợi lệnh run
init_db()

# --- ROUTES (ĐƯỜNG DẪN WEB) ---
@app.route('/')
def index():
    if not current_user.is_authenticated: return redirect(url_for('login'))
    if current_user.role == 'manager': return redirect(url_for('manager_dashboard'))
    elif current_user.role == 'staff': return redirect(url_for('staff_dashboard'))
    else: return redirect(url_for('customer_dashboard'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('index'))
        else: flash('Sai tên đăng nhập hoặc mật khẩu!', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/customer')
@login_required
def customer_dashboard():
    products = Product.query.all()
    return render_template('customer.html', products=products)

@app.route('/staff')
@login_required
def staff_dashboard():
    # Chỉ hiện đơn chưa hoàn thành
    orders = Order.query.filter(Order.status != 'Completed').order_by(Order.created_at.desc()).all()
    return render_template('staff.html', orders=orders)

@app.route('/manager', methods=['GET', 'POST'])
@login_required
def manager_dashboard():
    if current_user.role != 'manager': return "KHÔNG CÓ QUYỀN TRUY CẬP"
    
    # Xử lý thêm/xóa món
    if request.method == 'POST':
        if 'add_product' in request.form:
            name = request.form['name']
            price = float(request.form['price'])
            category = request.form['category']
            image_path = ""
            if 'image' in request.files:
                file = request.files['image']
                if file.filename != '':
                    filename = secure_filename(file.filename)
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                    image_path = f"/static/img/{filename}"

            new_prod = Product(name=name, price=price, category=category, image=image_path)
            db.session.add(new_prod)
            db.session.commit()
            flash('Thêm món thành công!', 'success')
            
        elif 'delete_product' in request.form:
            pid = request.form.get('product_id')
            Product.query.filter_by(id=pid).delete()
            db.session.commit()
            flash('Đã xóa món!', 'success')
            
        elif 'create_staff' in request.form:
             new_staff = User(
                username=request.form['username'],
                password=generate_password_hash(request.form['password']),
                full_name=request.form['full_name'],
                role='staff'
            )
             db.session.add(new_staff)
             db.session.commit()
             flash('Tạo nhân viên thành công!', 'success')
            
        return redirect(url_for('manager_dashboard'))

    products = Product.query.all()
    categories = sorted(list(set([p.category for p in products if p.category])))
    
    # Thống kê doanh thu
    completed_orders = Order.query.filter_by(status='Completed').all()
    total_revenue = sum(o.total_amount for o in completed_orders)
    
    return render_template('manager.html', products=products, categories=categories, total_revenue=total_revenue)

# --- SOCKET EVENTS (REAL-TIME) ---
@socketio.on('new_order_request')
def handle_new_order(data):
    # Lưu đơn hàng vào DB
    new_order = Order(
        customer_name=data['customer_name'],
        total_amount=data['total'],
        details=data['details'],
        status='Pending'
    )
    db.session.add(new_order)
    db.session.commit()
    
    # Báo cho nhân viên
    emit('update_staff_orders', {
        'id': new_order.id,
        'customer': new_order.customer_name,
        'details': new_order.details,
        'total': new_order.total_amount,
        'time': new_order.created_at.strftime("%H:%M")
    }, broadcast=True)

@socketio.on('staff_request_payment')
def handle_payment_request(data):
    order = Order.query.get(data['order_id'])
    if order:
        order.status = 'Paying'
        db.session.commit()
        # Báo cho khách hiện QR
        emit('show_customer_qr', {
            'total': order.total_amount, 
            'details': order.details,
            'customer_name': order.customer_name
        }, broadcast=True)

@socketio.on('staff_confirm_payment')
def handle_confirm_payment(data):
    order = Order.query.get(data['order_id'])
    if order:
        order.status = 'Completed'
        order.staff_id = current_user.id
        db.session.commit()
        emit('payment_success', {}, broadcast=True)

# --- CHẠY APP ---
if __name__ == '__main__':
    # Đoạn này chỉ chạy khi bạn test ở máy tính (Local)
    port = int(os.environ.get("PORT", 5001))
    print(f"--- SERVER LOCAL ĐANG CHẠY TẠI: http://127.0.0.1:{port} ---")
    socketio.run(app, host='0.0.0.0', port=port, debug=True, use_reloader=False, allow_unsafe_werkzeug=True)

