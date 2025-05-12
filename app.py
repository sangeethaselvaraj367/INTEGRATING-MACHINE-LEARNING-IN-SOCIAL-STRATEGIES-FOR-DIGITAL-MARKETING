from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from textblob import TextBlob
import os
from sqlalchemy import func, case
from datetime import datetime
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'your_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///ad_platform.db'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Advertisement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    price = db.Column(db.Float)
    description = db.Column(db.Text)
    image = db.Column(db.String(100))
    product_link = db.Column(db.String(255))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    likes = db.Column(db.Integer, default=0)
    unlikes = db.Column(db.Integer, default=0)
    feedback_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def update_feedback_stats(self):
        """Update feedback counts from the database"""
        feedback_stats = db.session.query(
            func.count(Feedback.id).label('total'),
            func.sum(case((Feedback.sentiment == 'positive', 1), else_=0)).label('positive'),
            func.sum(case((Feedback.sentiment == 'negative', 1), else_=0)).label('negative')
        ).filter_by(ad_id=self.id).first()
        
        self.feedback_count = feedback_stats.total or 0
        return {
            'total': self.feedback_count,
            'positive': feedback_stats.positive or 0,
            'negative': feedback_stats.negative or 0
        }

class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ad_id = db.Column(db.Integer, db.ForeignKey('advertisement.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    comment = db.Column(db.Text)
    sentiment = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Helper functions
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif'}

# Routes
@app.route('/')
def home():
    ads = Advertisement.query.order_by(Advertisement.likes.desc()).limit(3).all()
    return render_template('index.html', ads=ads)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = generate_password_hash(request.form['password'])
        
        if User.query.filter_by(username=username).first():
            flash('Username already exists')
            return redirect(url_for('register'))
            
        user = User(username=username, password=password)
        db.session.add(user)
        db.session.commit()
        flash('Registration successful! Please login.')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            session['username'] = user.username
            flash('Login successful!')
            return redirect(url_for('dashboard'))
        flash('Invalid username or password')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.')
    return redirect(url_for('home'))

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user_id = session['user_id']
    ads = Advertisement.query.filter_by(user_id=user_id).order_by(Advertisement.created_at.desc()).all()
    
    # Calculate dashboard metrics
    total_ads = len(ads)
    total_likes = db.session.query(func.sum(Advertisement.likes)).filter_by(user_id=user_id).scalar() or 0
    total_unlikes = db.session.query(func.sum(Advertisement.unlikes)).filter_by(user_id=user_id).scalar() or 0
    
    # Get feedback stats for all user's ads
    feedback_stats = db.session.query(
        func.count(Feedback.id).label('total'),
        func.sum(case((Feedback.sentiment == 'positive', 1), else_=0)).label('positive'),
        func.sum(case((Feedback.sentiment == 'negative', 1), else_=0)).label('negative')
    ).join(Advertisement, Feedback.ad_id == Advertisement.id)\
     .filter(Advertisement.user_id == user_id).first()
    
    total_feedback = feedback_stats.total or 0
    positive_feedback = feedback_stats.positive or 0
    negative_feedback = feedback_stats.negative or 0
    
    # Calculate rating metrics
    rating_score = positive_feedback - negative_feedback
    avg_rating = round((positive_feedback / total_feedback * 100) if total_feedback > 0 else 0, 1)
    
    # Performance overview (top 3 ads by likes)
    top_ads = sorted(ads, key=lambda x: x.likes, reverse=True)[:3]
    
    # Recent feedback
    recent_feedback = Feedback.query.join(Advertisement)\
                          .filter(Advertisement.user_id == user_id)\
                          .order_by(Feedback.created_at.desc())\
                          .limit(5).all()
    
    return render_template('dashboard.html', 
                         ads=ads,
                         Feedback=Feedback,
                         total_ads=total_ads,
                         total_likes=total_likes,
                         total_unlikes=total_unlikes,
                         total_feedback=total_feedback,
                         positive_feedback=positive_feedback,
                         negative_feedback=negative_feedback,
                         rating_score=rating_score,
                         avg_rating=avg_rating,
                         top_ads=top_ads,
                         recent_feedback=recent_feedback,
                         Advertisement=Advertisement)

@app.route('/add_ad', methods=['GET', 'POST'])
def add_ad():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        name = request.form['name']
        price = float(request.form['price'])
        description = request.form['description']
        product_link = request.form.get('product_link', '')
        
        if 'image' not in request.files:
            flash('No image uploaded')
            return redirect(request.url)
            
        image = request.files['image']
        if image.filename == '':
            flash('No selected image')
            return redirect(request.url)
            
        if not allowed_file(image.filename):
            flash('Invalid file type')
            return redirect(request.url)
            
        filename = secure_filename(image.filename)
        image.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        ad = Advertisement(
            name=name,
            price=price,
            description=description,
            product_link=product_link,
            image=filename,
            user_id=session['user_id'],
            feedback_count=0
        )
        db.session.add(ad)
        db.session.commit()
        flash('Advertisement added successfully!')
        return redirect(url_for('dashboard'))
    return render_template('add_ad.html')

@app.route('/edit_ad/<int:ad_id>', methods=['GET', 'POST'])
def edit_ad(ad_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    ad = Advertisement.query.get_or_404(ad_id)
    
    if ad.user_id != session['user_id']:
        flash('You can only edit your own ads')
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        ad.name = request.form['name']
        ad.price = float(request.form['price'])
        ad.description = request.form['description']
        ad.product_link = request.form.get('product_link', '')
        
        if 'image' in request.files:
            image = request.files['image']
            if image.filename != '':
                if allowed_file(image.filename):
                    filename = secure_filename(image.filename)
                    image.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                    ad.image = filename
                else:
                    flash('Invalid file type')
                    return redirect(request.url)
        
        db.session.commit()
        flash('Advertisement updated successfully!')
        return redirect(url_for('dashboard'))
    
    return render_template('edit_ad.html', ad=ad)

@app.route('/delete_ad/<int:ad_id>', methods=['POST'])
def delete_ad(ad_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    ad = Advertisement.query.get_or_404(ad_id)
    
    if ad.user_id != session['user_id']:
        flash('You can only delete your own ads')
        return redirect(url_for('dashboard'))
    
    # Delete associated feedback first
    Feedback.query.filter_by(ad_id=ad_id).delete()
    
    # Delete the ad
    db.session.delete(ad)
    db.session.commit()
    
    flash('Advertisement deleted successfully!')
    return redirect(url_for('dashboard'))

@app.route('/insta')
def most_liked_ads():
    ads = Advertisement.query.order_by(Advertisement.likes.desc()).all()
    return render_template('most_liked_ads.html', ads=ads)

@app.route('/most_commented')
def most_commented_ads():
    ads = Advertisement.query.order_by(Advertisement.feedback_count.desc()).all()
    return render_template('most_commented_ads.html', ads=ads)

@app.route('/like/<int:ad_id>')
def like(ad_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    ad = Advertisement.query.get_or_404(ad_id)
    ad.likes += 1
    db.session.commit()
    flash('You liked this ad!')
    return redirect(url_for('most_liked_ads'))

@app.route('/unlike/<int:ad_id>')
def unlike(ad_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    ad = Advertisement.query.get_or_404(ad_id)
    ad.unlikes += 1
    db.session.commit()
    flash('You unliked this ad!')
    return redirect(url_for('most_liked_ads'))

@app.route('/feedback/<int:ad_id>', methods=['GET', 'POST'])
def feedback(ad_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    ad = Advertisement.query.get_or_404(ad_id)
    
    if request.method == 'POST':
        comment = request.form['comment']
        if not comment.strip():
            flash('Comment cannot be empty!')
            return redirect(url_for('feedback', ad_id=ad_id))
            
        sentiment_score = TextBlob(comment).sentiment.polarity
        sentiment = 'positive' if sentiment_score > 0 else 'negative' if sentiment_score < 0 else 'neutral'
        
        fb = Feedback(
            ad_id=ad_id,
            user_id=session['user_id'],
            comment=comment,
            sentiment=sentiment
        )
        db.session.add(fb)
        ad.feedback_count += 1
        db.session.commit()
        flash('Thank you for your feedback!')
        return redirect(url_for('most_liked_ads'))
    
    return render_template('feedback.html', ad=ad)

@app.route('/analysis/<int:ad_id>')
def analysis(ad_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    ad = Advertisement.query.get_or_404(ad_id)
    feedbacks = Feedback.query.filter_by(ad_id=ad_id).order_by(Feedback.created_at.desc()).all()
    
    total = len(feedbacks)
    pos = len([f for f in feedbacks if f.sentiment == 'positive'])
    neg = len([f for f in feedbacks if f.sentiment == 'negative'])
    neu = len([f for f in feedbacks if f.sentiment == 'neutral'])
    
    # Calculate percentages
    pos_percent = round((pos / total) * 100, 2) if total > 0 else 0
    neg_percent = round((neg / total) * 100, 2) if total > 0 else 0
    neu_percent = round((neu / total) * 100, 2) if total > 0 else 0
    
    # Sentiment over time data
    sentiment_data = []
    for fb in feedbacks:
        sentiment_data.append({
            'date': fb.created_at.strftime('%Y-%m-%d'),
            'sentiment': fb.sentiment,
            'comment': fb.comment
        })
    
    return render_template('analysis.html', 
                         ad=ad,
                         pos=pos, 
                         neg=neg, 
                         neu=neu, 
                         total=total,
                         pos_percent=pos_percent,
                         neg_percent=neg_percent,
                         neu_percent=neu_percent,
                         feedbacks=feedbacks,
                         sentiment_data=sentiment_data)

@app.cli.command('update-feedback-counts')
def update_feedback_counts():
    """Update all ads with their current feedback counts"""
    ads = Advertisement.query.all()
    for ad in ads:
        ad.feedback_count = Feedback.query.filter_by(ad_id=ad.id).count()
    db.session.commit()
    print(f"Updated feedback counts for {len(ads)} ads")

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
