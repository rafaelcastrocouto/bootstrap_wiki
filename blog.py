import os
import re
import random
import hashlib
import hmac
import logging
import json
from datetime import datetime, timedelta
from string import letters

import webapp2
import jinja2

from google.appengine.ext import db
from google.appengine.api import memcache

template_dir = os.path.join(os.path.dirname(__file__), 'templates')
jinja_env = jinja2.Environment(loader = jinja2.FileSystemLoader(template_dir),
                               autoescape = True)

secret = 'imnosecretatall'

def render_str(template, **params):
    t = jinja_env.get_template(template)
    return t.render(params)

def make_secure_val(val):
    return '%s|%s' % (val, hmac.new(secret, val).hexdigest())

def check_secure_val(secure_val):
    val = secure_val.split('|')[0]
    if secure_val == make_secure_val(val):
        return val

class Handler(webapp2.RequestHandler):
    def write(self, *a, **kw):
        self.response.out.write(*a, **kw)

    def render_str(self, template, **params):
        params['user'] = self.user
        return render_str(template, **params)

    def render(self, template, **kw):
        self.write(self.render_str(template, **kw))

    def render_json(self, d):
        json_txt = json.dumps(d)
        self.response.headers['Content-Type'] = 'application/json; charset=UTF-8'
        self.write(json_txt)

    def set_secure_cookie(self, name, val):
        cookie_val = make_secure_val(val)
        self.response.headers.add_header(
            'Set-Cookie',
            '%s=%s; Path=/' % (name, cookie_val))

    def read_secure_cookie(self, name):
        cookie_val = self.request.cookies.get(name)
        return cookie_val and check_secure_val(cookie_val)

    def login(self, user):
        self.set_secure_cookie('user_id', str(user.key().id()))

    def logout(self):
        self.response.headers.add_header('Set-Cookie', 'user_id=; Path=/')

    def initialize(self, *a, **kw):
        webapp2.RequestHandler.initialize(self, *a, **kw)
        uid = self.read_secure_cookie('user_id')
        self.user = uid and User.by_id(int(uid))

        if self.request.url.endswith('.json'):
            self.format = 'json'
        else:
            self.format = 'html'

    def notfound(self):
        self.error(404)
        self.write('404 error: Not found =(')

##### regexp

PAGE_RE = r'(/(?:[a-zA-Z0-9_-]+/?)*)'

USER_RE = re.compile(r"^[a-zA-Z0-9_-]{3,20}$")
def valid_username(username):
    return username and USER_RE.match(username)

PASS_RE = re.compile(r"^.{3,20}$")
def valid_password(password):
    return password and PASS_RE.match(password)

EMAIL_RE  = re.compile(r'^[\S]+@[\S]+\.[\S]+$')
def valid_email(email):
    return not email or EMAIL_RE.match(email)

##### user stuff
def make_salt(length = 5):
    return ''.join(random.choice(letters) for x in xrange(length))

def make_pw_hash(name, pw, salt = None):
    if not salt:
        salt = make_salt()
    h = hashlib.sha256(name + pw + salt).hexdigest()
    return '%s,%s' % (salt, h)

def valid_pw(name, password, h):
    salt = h.split(',')[0]
    return h == make_pw_hash(name, password, salt)

def users_key(group = 'default'):
    return db.Key.from_path('users', group)

class User(db.Model):
    name = db.StringProperty(required = True)
    pw_hash = db.StringProperty(required = True)
    email = db.StringProperty()

    @classmethod
    def by_id(cls, uid):
        return User.get_by_id(uid, parent = users_key())

    @classmethod
    def by_name(cls, name):
        u = User.all().filter('name =', name).get()
        return u

    @classmethod
    def register(cls, name, pw, email = None):
        pw_hash = make_pw_hash(name, pw)
        return User(parent = users_key(),
                    name = name,
                    pw_hash = pw_hash,
                    email = email)

    @classmethod
    def login(cls, name, pw):
        u = cls.by_name(name)
        if u and valid_pw(name, pw, u.pw_hash):
            return u

class Signup(Handler):
    def get(self):
        next_url = self.request.headers.get('referer', '/')
        self.render("signup.html", next_url = next_url)

    def post(self):
        have_error = False

        next_url = str(self.request.get('next_url'))
        if not next_url or next_url.startswith('/login'):
            next_url = '/'

        self.username = self.request.get('username')
        self.password = self.request.get('password')
        self.verify = self.request.get('verify')
        self.email = self.request.get('email')

        params = dict(username = self.username,
                      email = self.email)

        if not valid_username(self.username):
            params['error_username'] = "That's not a valid username."
            have_error = True

        if not valid_password(self.password):
            params['error_password'] = "That wasn't a valid password."
            have_error = True
        elif self.password != self.verify:
            params['error_verify'] = "Your passwords didn't match."
            have_error = True

        if not valid_email(self.email):
            params['error_email'] = "That's not a valid email."
            have_error = True

        if have_error:
            self.render('signup.html', **params)
        else:
            u = User.register(self.username, self.password, self.email)
            u.put()

            self.login(u)
            self.redirect(next_url)

class Login(Handler):
    def get(self):
        next_url = self.request.headers.get('referer', '/')
        self.render('login.html', next_url = next_url)

    def post(self):
        username = self.request.get('username')
        password = self.request.get('password')

        next_url = str(self.request.get('next_url'))
        if not next_url or next_url.startswith('/login'):
            next_url = '/'

        u = User.login(username, password)
        if u:
            self.login(u)
            self.redirect('/')
        else:
            msg = 'Invalid login'
            self.render('login.html', error = msg)
            

class Logout(Handler):
    def get(self):
        next_url = self.request.headers.get('referer', '/')
        self.logout()
        self.redirect(next_url)


#### cache stuff

def age_set(key, val):
    save_time = datetime.utcnow()
    memcache.set(key, (val, save_time))

def age_get(key):
    r = memcache.get(key)
    if r:
        val, save_time = r
        age = (datetime.utcnow() - save_time).total_seconds()
    else: 
        val, age = None, 0

    return val, age

def age_str(age):
    s = 'queried %s seconds ago'
    age = int(age)
    if age == 1:
        s = 'queried %s second ago'
    return s % age


class Flush(Handler):
    def get(self):
        memcache.flush_all()
        self.redirect('/')

##### page stuff

class Page(db.Model):
    content = db.TextProperty(required = True)
    #author = db.ReferenceProperty(User, required = True)
    created = db.DateTimeProperty(auto_now_add = True)
    last_modified = db.DateTimeProperty(auto_now = True)

    @staticmethod
    def parent_key(path):
        return db.Key.from_path('/root' + path, 'pages')

    @classmethod
    def by_path(cls, path):
        q = cls.all()
        q.ancestor(cls.parent_key(path))
        q.order("-created")
        return q

    @classmethod
    def by_id(cls, page_id, path):
        return cls.get_by_id(page_id, cls.parent_key(path))


class EditPage(Handler):
    def get(self, path):
        if not self.user:
            self.redirect('/login')

        v = self.request.get('v')
        p = None
        if v:
            if v.isdigit():
                p = Page.by_id(int(v), path)
            if not p:
                return self.notfound()
        else:
            p = Page.by_path(path).get()

        self.render("edit.html", path = path, page = p)


    def post(self, path):
        if not self.user:
            self.error(400)
            return

        content = self.request.get('content')
        old_page = Page.by_path(path).get()

        if not (old_page or content):
            return
        elif not old_page or old_page.content != content:
            p = Page(parent = Page.parent_key(path), content = content)
            p.put()
        self.redirect(path)

class HistoryPage(Handler):
    def get(self, path):
        q = Page.by_path(path)
        q.fetch(limit = 100)

        posts = list(q)
        if posts:
            self.render('history.html', path = path, posts = posts)
        else:
            self.redirect('/_edit' + path)

class WikiPage(Handler):
    def get(self, path):

        v = self.request.get('v')
        p = None
        if v:
            if v.isdigit():
                p = Page.by_id(int(v), path)
            if not p:
                return self.notfound()
        else:
            p = Page.by_path(path).get()

        if p:
            self.render('page.html', path = path, page = p)
        else:
            self.redirect('/_edit' + path)


app = webapp2.WSGIApplication([
                                ('/signup', Signup),
                                ('/login', Login),
                                ('/logout', Logout),
                                ('/_flush', Flush),
                                ('/_history' + PAGE_RE, HistoryPage),
                                ('/_edit' + PAGE_RE, EditPage),
                                (PAGE_RE, WikiPage)
                            ],
                            debug=True)
