import datetime
import json
import os
from flask import Flask, flash, render_template, request, session, redirect
from flask_sqlalchemy import SQLAlchemy
from venmo_api import random_device_id
import venmo_request
app = Flask(__name__)


# with open("sensitive.txt", "r") as handle:
#     data = handle.read()

# config = json.loads(data)
# secret = config["secret"]

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.sqlite3'
app.secret_key = os.environ.get('SECRET_KEY', None)
db = SQLAlchemy(app)

app.config["IMAGE_UPLOADS"] = "static/images"


class Post(db.Model):
    '''
    Creates the Post table for the users db,
    each Post has an id, owner, creation date, a unique access token, a title,
    a description, a total money raised goal, a minimum contribution amount,
    a last updated date, and an image location string. Also has a relationship
    with the Donations table.
    '''
    id = db.Column(db.Integer, primary_key=True)
    owner = db.Column(db.String(60), nullable=False)
    created_date = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    access_token = db.Column(db.String(120), unique=True, nullable=False)
    title = db.Column(db.String(120), unique=False, nullable=False)
    description = db.Column(db.Text, unique=False, nullable=False)
    goal = db.Column(db.Float, unique=False, nullable=False)
    min_contribution = db.Column(db.Float, unique=False, nullable=False)
    last_updated = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    image_location = db.Column(db.String(120), default='venmo_logo.jpg')
    donations = db.relationship('Donations', backref='post', lazy=True)

    def __repr__(self):
        return '<Post %r>' % self.image_location


class Donations(db.Model):
    '''
    Keeps track of the donations each Post has. Each record has an id, the username of the donor, 
    a donation amount, and a foreign key of its post id.
    '''
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(60), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)


@app.route("/")
def index():
    '''
    Queries the Post table and returns all posts, 
    also checks donation progress on each invidual post.
    Returns the index.html template.
    '''
    posts = Post.query.all()
    progress = dict()
    for post in posts:
        progress[post] = sum(donation.amount for donatoin in post.donations)
    return render_template("index.html", posts=posts, progress=progress)


@app.route("/create", methods=["POST", "GET"])
def create():
    '''
    Stores information from create form in a session.
    Redirects users to the "/login" route if form completion
    is successful.
    '''
    if request.method == "POST":
        image = request.files["image"]
        if image.filename != "":
            image.save(os.path.join(
                app.config["IMAGE_UPLOADS"], image.filename))
            session["image"] = image.filename
        else:
            session["image"] = "venmo_logo.jpg"
        session["title"] = request.form["title"]
        session["description"] = request.form["description"]
        session["goal"] = request.form["goal"]
        session["min_contribution"] = request.form["min_contribution"]
        return redirect("/login")

    return render_template("create.html")


@app.route("/crowdmo/<id>", methods=["POST", "GET"])
def crowdmo(id):
    '''
    Selects post within given Id froms Post table.
    Checks to see when the last time the Post's donors were
    updated. If it has been more than 2 hours since update
    it reupdates the donors. This check is done to avoid hitting the Venmo
    API too much and being locked out.

    Updates the Donors table if there have been new donors.
    '''
    # check the last time a post was updated, and if it has been greater than 2 hours, update again
    post = Post.query.filter_by(id=id).first()
    if abs(datetime.datetime.utcnow().hour - post.last_updated.hour) > 2:
        print("UPDATING DONORS")
        donors = venmo_request.update_contributors(
            search_note=post.title, access_token=post.access_token, creation_date=post.last_updated)
        for donor in donors.keys():
            new_donor = Donations(
                username=donor, amount=donors[donor], post_id=post.id)
            db.session.add(new_donor)
        post.last_updated = datetime.datetime.utcnow()
        db.session.commit()

    # create dictionary of donors from database
    donors = dict()
    for donation in post.donations:
        donors[donation.username] = donation.amount
    progress = venmo_request.calc_progress_to_goal(donors=donors)

    if request.method == "POST":
        venmo_username = request.form["venmo_username"]
        friend_id = venmo_request.find_user_for_request(
            venmo_username, post.access_token)
        if friend_id != False:
            venmo_request.request_friend(
                user_id=friend_id, min_amount=post.min_contribution, access_token=post.access_token, note=post.title)
    return render_template("crowdfund.html", post=post, progress=progress, donors=donors)


@app.route("/login", methods=["POST", "GET"])
def login():
    '''
    Prompts user for Venmo login information,
    generates one time password secret using
    login info -- stores this information in the session.

    Redirects user to /two_factor if username 
    and password do not return any errors.
    '''
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        session["device_id"] = random_device_id()
        otp_secret = venmo_request.venmo_get_otp(
            username, password, session["device_id"])
        session["username"] = username
        session["password"] = password
        session["otp_secret"] = otp_secret

        print(f"storing otp_secret in session={session['otp_secret']}")
        return redirect("/two_factor")
    return render_template("login.html")


@app.route("/two_factor", methods=["POST", "GET"])
def two_factor():
    ''' 
    Prompts user for one time password code
    that is sent to the user's phone #. Creates
    an access token using the users otp secret
    and the otp code that the user inputs.

    After the user finishes 2FA their Post is added
    post table, and they are redirected to "/"
    '''
    if "otp_secret" in session:
        if request.method == "POST":
            session["otp_code"] = request.form["otp_code"]
            print(
                f"two_factor using: otp_code={session['otp_code']}    otp_secret={session['otp_secret']}")
            session["access_token"] = venmo_request.venmo_get_access_token(
                otp_secret=session["otp_secret"], otp_code=session["otp_code"], device_id=session["device_id"])
            print(f"access token: {session['access_token']}")
            create_post()
            return redirect("/")
        return render_template("two_factor.html")
    flash("You must input a username and password first to do that!")
    return redirect("/login")


def create_post():
    '''
    Adds a new record to the post table.
    '''
    if session["access_token"] != None:
        newPost = Post(access_token=session["access_token"], title=session["title"], image_location="images/" + session["image"],
                       owner=session["username"], description=session["description"], goal=session["goal"], min_contribution=session["min_contribution"])
        db.session.add(newPost)
        db.session.commit()


if __name__ == "__main__":
    db.create_all()
