
#Todo user disconnectivity events and ending a game sooner.

import os
import time
from random import randint
import numpy as np
import flask_socketio
from flask import Flask, request, render_template
from flask_socketio import SocketIO,emit
from flask_caching import Cache

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
    socketio = flask_socketio.SocketIO(app, message_queue=f'redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/0')
    app.logger.info('Running cache on redis host: '+REDIS_HOST)
except:
    cache = Cache(config={'CACHE_TYPE': 'SimpleCache'})
    socketio = flask_socketio.SocketIO(app)
    app.logger.warning('Running local memory cache, make sure you are running only one replica or running outside of a kubernetes cluster')

cache.init_app(app)

def decode_rle(rle_str):
    return ''.join(c * int(n or 1) for c, n in zip(rle_str[::2], rle_str[1::2]))

def string2array(board):
    temp_board = np.zeros((GRID_SIZE, GRID_SIZE), dtype=bool)
    for y,line in enumerate(list(map(''.join, zip(*[iter(board)]*GRID_SIZE)))):
        for x,chr in enumerate(line):
            if chr == 'T':
                temp_board[x,y] = True
            else:
                temp_board[x, y] = False
    return temp_board

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

def end_game_soon(room_id, losing_player):
    # Clear cache
    # Emit end game
    return NotImplemented

# The route() function of the Flask class is a decorator,
# which tells the application which URL should call
# the associated function.
@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('create_room')
def create_room(alias, iterations):
    if cache.get('users:room#'+request.sid) or not isinstance(iterations,int):
        # Emit error
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
    emit('join_room', {'room': room_id,'room_admin':alias,'users':[cache.get('users:alias#'+request.sid)]}, room=room_id)

@socketio.on('join_room')
def join_room(alias,room_id):
    users = cache.get('rooms:users#'+room_id)
    if not users or request.sid in users or len(users) >= MAX_LOBBY_PLAYERS or cache.get('rooms:board#'+room_id) or cache.get('users:room#'+request.sid):
        # Emit error
        return
    flask_socketio.join_room(room_id)
    cache.set('rooms:users#'+room_id, users+[request.sid])
    cache.set('users:room#' + request.sid, room_id)
    cache.set('users:alias#' + request.sid, alias)
    emit('join_room', {'room': room_id,'room_admin':cache.get('users:alias#'+users[0]),'users':[cache.get('users:alias#'+user) for user in users+[request.sid]]}, room=room_id)

@socketio.on('init_game')
def init_game():
    room_id = cache.get('users:room#' + request.sid)
    if not room_id:
        # Emit error
        return
    users = cache.get('rooms:users#' + room_id)
    if not users or len(users) < 2 or users[0] != request.sid:
        # Emit error
        return

    #Initiating the room game
    cache.set('rooms:board#' + room_id, np.zeros((GRID_SIZE, GRID_SIZE), dtype=bool))
    cache.set('rooms:users-left#' + room_id, users)
    for idx,user in enumerate(users):
        emit('init_game', {'room': room_id, 'position': idx}, sid=user)

@socketio.on('send_board')
def send_board(board):
    room_id = cache.get('users:room#'+request.sid)
    if not room_id:
        # Emit error
        return
    if board == 'False':
        end_game_soon(room_id,request.sid)
        return
    game_board = cache.get('rooms:board#' + room_id)
    users_left = cache.get('rooms:users-left#' + room_id)
    if game_board is None or request.sid not in users_left:
        # Emit error
        return
    cache.set('rooms:board#'+room_id,np.logical_and(cache.get('rooms:board#'+room_id),string2array(decode_rle(board))))
    cache.set('rooms:users-left#'+room_id, [sid for sid in 'rooms:users-left#'+room_id if sid != request.sid])
    if len(cache.get('rooms:users-left#'+room_id)) == 0:
        board_array = cache.get('rooms:board#'+room_id)
        # Todo convert array to rle encoded string
        emit('game_start', {'room': room_id, 'game_board':board_array, 'iterations':cache.get('rooms:iterations#'+room_id)}, room=room_id)
        clear_room_cache(room_id)



# main driver function
if __name__ == '__main__':

    # run() method of Flask class runs the application
    # on the local development server.
    socketio.run(app,host="0.0.0.0", port=5000,allow_unsafe_werkzeug=True)
