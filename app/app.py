async_mode = None

if async_mode is None:
    try:
        import eventlet
        async_mode = 'eventlet'
    except ImportError:
        pass

    if async_mode is None:
        try:
            from gevent import monkey
            async_mode = 'gevent'
        except ImportError:
            pass

    if async_mode is None:
        async_mode = 'threading'

    print('async_mode is ' + async_mode)

# monkey patching is necessary because this application uses a background
# thread
if async_mode == 'eventlet':
    import eventlet
    eventlet.monkey_patch()
elif async_mode == 'gevent':
    from gevent import monkey
    monkey.patch_all()

import itertools
import os
import time
from random import randint
import numpy as np
import flask_socketio
from flask import Flask, request, render_template
from flask_socketio import SocketIO,emit,send
from flask_caching import Cache
import threading
import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

MAX_LOBBY_PLAYERS = 8
GRID_SIZE = 50
#Cache keys:
# rooms:users#{room_id} -> []
# rooms:users-left#{room_id} -> []
# rooms:board#{room_id} -> [[]]
# rooms:iterations#{room_id} -> int

# users:room#{user_sid} -> int
# users:alias#{user_sid} -> str

app = Flask(__name__)

try:
    REDIS_HOST = os.getenv('REDIS_HOST').strip()
    REDIS_PASSWORD = os.environ.get('REDIS_PASSWORD').strip()
    REDIS_PORT = os.environ.get('REDIS_PORT').strip()

    cache = Cache(config={'CACHE_TYPE': 'RedisCache','CACHE_REDIS_HOST':REDIS_HOST,'CACHE_REDIS_PORT':REDIS_PORT,'CACHE_REDIS_PASSWORD':REDIS_PASSWORD})
    socketio = flask_socketio.SocketIO(app, message_queue=f'redis://default:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/0')
    app.logger.info('Running cache on redis host: '+REDIS_HOST)
except:
    cache = Cache(config={'CACHE_TYPE': 'SimpleCache'})
    socketio = flask_socketio.SocketIO(app)
    app.logger.warning('Running local memory cache, make sure you are running only one replica or running outside of a kubernetes cluster')

cache.init_app(app)

def decode_rle(rle_str):
    return ''.join(c * int(n or 1) for c, n in zip(rle_str[::2], rle_str[1::2]))

def encode_rle(str):
    return "".join([f"{str[i]}{len(list(group))}" for i, group in itertools.groupby(str)])

def string2array(board):
    return np.array(list(board)).reshape(50, 50) == "T"

def clear_room_cache(room_id):
    cache.delete('rooms:users-left#'+room_id)
    cache.delete('rooms:board#' + room_id)
    cache.delete('rooms:iterations#' + room_id)
    users = cache.get('rooms:users#'+room_id)
    cache.delete('rooms:users#' + room_id)
    if users:
        for user in users:
            cache.delete('users:room#' + user)
            cache.delete('users:alias#' + user)

def end_game_soon(room_id):
    with app.test_request_context('/'):
        emit('error', {'message': 'A user has disconnected in middle of the game.'}, room=room_id, namespace='/')
    clear_room_cache(room_id)

def clear_player_cache(sid):
    room_id = cache.get('users:room#'+sid)
    if not room_id:
        return
    users = cache.get('rooms:users#'+room_id)
    board = cache.get('rooms:board#'+room_id)
    if board or users[0] == sid:
        end_game_soon(room_id)
    else:
        users.remove(sid)
        cache.set('rooms:users#' + room_id, users)
        cache.delete('users:room#' + sid)
        cache.delete('users:alias#' + sid)
        with app.test_request_context('/'):
            emit('room_info', {'room': room_id,'room_admin':cache.get('users:alias#'+users[0]),'room_admin_id':users[0],'users':[cache.get('users:alias#'+user) for user in users]}, room=room_id,namespace='/')

#Every 1 second ping every connected user to dynamically remove players from rooms when inactive
class global_users:
    active_users = []
    answered_users = []

def ack(value):
    global_users.answered_users.append(value)

def ping():
    socketio.emit('ping', {}, callback=ack)
    time.sleep(1)
    for disconnected_user in list(set(global_users.active_users) - set(global_users.answered_users)):
        global_users.active_users.remove(disconnected_user)
        clear_player_cache(disconnected_user)
    global_users.answered_users = []
    threading.Timer(0,ping).start()


@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('create_room')
def create_room(alias, iterations):
    if cache.get('users:room#'+request.sid) or not iterations:
        emit('error', {'message': 'Couldn\'t create room.'}, broadcast=False)
        return
    room_id = randint(1000,9999)
    while room_id in socketio.server.manager.rooms['/']:
        room_id = randint(1000, 9999)
    room_id = str(room_id)
    flask_socketio.join_room(room_id)
    cache.set('rooms:users#'+room_id,[request.sid])
    cache.set('users:room#'+request.sid, room_id)
    cache.set('users:alias#' + request.sid, alias)
    cache.set('rooms:iterations#'+room_id,iterations)
    global_users.active_users.append(request.sid)
    print(alias, "Created a room")
    emit('room_info', {'room': room_id,'room_admin':alias, 'room_admin_id':request.sid,'users':[cache.get('users:alias#'+request.sid)]}, room=room_id)

@socketio.on('join_room')
def join_room(alias,room_id):
    users = cache.get('rooms:users#'+room_id)
    if not users or request.sid in users or len(users) >= MAX_LOBBY_PLAYERS or cache.get('rooms:board#'+room_id) or cache.get('users:room#'+request.sid):
        emit('error', {'message': 'Couldn\'t join room.'}, broadcast=False)
        return
    flask_socketio.join_room(room_id)
    cache.set('rooms:users#'+room_id, users+[request.sid])
    cache.set('users:room#' + request.sid, room_id)
    cache.set('users:alias#' + request.sid, alias)
    global_users.active_users.append(request.sid)
    print(alias, "Joined a room")
    emit('room_info', {'room': room_id,'room_admin':cache.get('users:alias#'+users[0]),'room_admin_id':users[0],'users':[cache.get('users:alias#'+user) for user in users+[request.sid]]}, room=room_id)

@socketio.on('init_game')
def init_game():
    room_id = cache.get('users:room#' + request.sid)
    if not room_id:
        emit('error', {'message': 'User not in a room.'}, broadcast=False)
        return
    users = cache.get('rooms:users#' + room_id)
    if not users or len(users) < 2 or users[0] != request.sid:
        emit('error', {'message': 'User is not an admin or game has less than two players.'}, broadcast=False)
        return

    #Initiating the room game
    cache.set('rooms:board#' + room_id, np.zeros((GRID_SIZE, GRID_SIZE), dtype=bool))
    cache.set('rooms:users-left#' + room_id, users)
    for idx,user in enumerate(users):
        send({'room': room_id, 'position': idx}, to=user)

@socketio.on('send_board')
def send_board(board):
    room_id = cache.get('users:room#'+request.sid)
    if not room_id:
        emit('error', {'message': 'User not in a room.'}, broadcast=False)
        return
    if board == 'False':
        end_game_soon(room_id)
        return
    game_board = cache.get('rooms:board#' + room_id)
    users_left = cache.get('rooms:users-left#' + room_id)
    if game_board is None or request.sid not in users_left:
        emit('error', {'message': 'Game hasn\'t started yet.'}, broadcast=False)
        return
    cache.set('rooms:board#'+room_id,np.logical_and(cache.get('rooms:board#'+room_id),string2array(decode_rle(board))))
    cache.set('rooms:users-left#'+room_id, [sid for sid in 'rooms:users-left#'+room_id if sid != request.sid])
    if len(cache.get('rooms:users-left#'+room_id)) == 0:
        board_array = cache.get('rooms:board#'+room_id)
        board_string = np.array2string(board_array, separator='').replace(' ', '').replace('\n').replace('0', 'F').replace('1', 'T')
        emit('game_start', {'room': room_id, 'game_board':encode_rle(board_string), 'iterations':cache.get('rooms:iterations#'+room_id)}, room=room_id)
        cache.delete('rooms:board#' + room_id)
        cache.delete('rooms:users-left#' + room_id)






# main driver function
if __name__ == '__main__':
    # run() method of Flask class runs the application
    # on the local development server.
    ping_thread = threading.Thread(target=ping, name="ping")
    ping_thread.start()
    socketio.run(app,host="0.0.0.0", port=5000)
