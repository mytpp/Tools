#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'Personal Network Storage'

__author__ = 'mytpp'


import sys
import os
import shutil
import argparse
import yaml
import json
import asyncio
import aiosqlite3
import hashlib
import datetime
import time
import socket

import logging
logging.basicConfig(level=logging.INFO)

# handle Ctrl+C
import signal
signal.signal(signal.SIGINT, signal.SIG_DFL)


config = None # to be read from *.yaml
metaDB = None # meta database used only in tracker

# used by tracker to record living daemons in the pns
# map hostname to its timestamp
daemons = {}


def parse_config(file_name):
    # read config from *.yaml
    wd = os.path.split(os.path.realpath(__file__))[0]
    yaml_path = os.path.join(wd, file_name)
    global config 
    with open(yaml_path, 'r', encoding='utf-8') as f:
        content = f.read()
    config = yaml.load(content)
    config['root'] = config['root'].rstrip('/')
    config['port'] = str(config['port'])
    tracker_addr = config['tracker'].split(':')
    config['tracker_ip'] = tracker_addr[0]
    config['tracker_port'] = tracker_addr[1]
    # this_ip = socket.gethostbyname(socket.gethostname())
    this_ip = '127.0.0.1'
    config['ip'] = this_ip

    logging.info('Load config successfully')
    logging.debug(config)

def parse_header(header_str):
    header_str = header_str.strip()
    fields = header_str.split('\n')
    logging.debug(f"Received header:\n{fields!r}")
    header = {}
    for line in fields:
        word_list = line.split(': ')
        if len(word_list) < 2:
           continue
        key, value = word_list
        header[key] = value
    return header

# construct a request header
def make_header(cmd, length=0, is_heartbeat=False):
    sha1 = hashlib.sha1()
    sha1.update(config['secret'].encode('utf-8'))
    sha1.update(cmd.encode('utf-8'))
    if is_heartbeat:
        header  = 'V: ' + config['name'] + ' HB' + '\n'
    else:
        header  = 'V: ' + config['name'] + ' V1' + '\n'
    header += 'A: ' + sha1.hexdigest() + '\n'
    header += 'C: ' + cmd + '\n'
    if length > 0:
        header += 'L: ' + str(length) + '\n'
    header += '\n'
    return header

# get error code
async def get_error(reader, writer):
    error = await reader.readuntil(b'\n\n') # 'error' is like b'E: 200 OK'
    err_msg = error.decode('utf-8').strip() 
    logging.info(err_msg)
    if err_msg.split(' ', 2)[1] != '200': # error occurs
        writer.close()
        return True

async def send_file(src, dst, writer, loop):
    logging.info('Start sending file...')
    size = os.path.getsize(src)
    with open(src, 'rb') as f:
        tr = writer.transport
        # 'size' and 'dst' is the reason why we need this header
        header = make_header('cp ' + src + ' ' + dst, size)
        logging.debug(f'Send: {header!r}')
        writer.write(header.encode('utf-8'))
        await writer.drain()
        # send file
        await loop.sendfile(tr, f) 
        writer.write_eof()
        await writer.drain()
    logging.info('Finish sending file.')


################################################################################
#---------------------------------Daemon Side----------------------------------#

#----------------------------Database Initiliztion-----------------------------#
def root_to_relative(localpath):
    return localpath.split(config['root'])[1]

def root_to_physical(path, in_default_root=True):
    global config
    if os.path.isdir(path): path += '/'  # identify a dir
    if in_default_root:
        path = root_to_relative(path)
    else:
        path = '/' + path
    return '//' + config['ip'] + ':' + config['port'] + path

async def load_path(root, logical_root=None, cursor=None, 
                    in_default_root=True, is_tracker=True):
    global config
    if os.path.isdir(root):
        path_list = [root + subpath for subpath in os.listdir(root)]
    else:
        path_list = [root]
    for path in path_list:
        if is_tracker: # this daemon has the meta database
            logging.info('loading ' + path)
            ctime_stamp = os.path.getctime(path)
            mtime_stamp = os.path.getmtime(path)
            ctime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ctime_stamp))
            mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime_stamp))
            await cursor.execute('''
                insert into filesystem
                (physical_path, category, ctime, mtime, size, host_addr, host_name)
                values  (?, ?, ?, ?, ?, ?, ?)
                ''', 
                (root_to_relative(path) , os.path.isfile(path), 
                ctime, mtime, os.path.getsize(path), 
                config['ip'] + ':' + config['port'], config['name'])
            )
            await metaDB.commit()
        else:  # this side don't have the meta database
            if logical_root:
                logical_path = logical_root + os.path.split(path)[1]
                cmd = 'ln ' + root_to_physical(path, in_default_root) + ' ' + logical_path
            else:
                cmd = 'ln ' + root_to_physical(path, in_default_root)
            header = make_header(cmd, os.path.getsize(path))

            reader, writer = await asyncio.open_connection(
                            config['tracker_ip'], config['tracker_port'])
            logging.debug(f'Send: {header!r}')
            writer.write(header.encode('utf-8'))
            await writer.drain()
            if await get_error(reader, writer):
                return
            logging.info('Link ' + path + ' successfully')
            writer.close()

        # recursive load path
        if os.path.isdir(path):
            if logical_root:
                await load_path(path + '/', cursor=cursor, 
                    logical_root=logical_root + os.path.split(path)[1] + '/', 
                    in_default_root=in_default_root, is_tracker=is_tracker)
            else:
                await load_path(path + '/', cursor=cursor, 
                    in_default_root=in_default_root, is_tracker=is_tracker)

async def update_db():
    global config, metaDB
    path = config['root']
    if config['istracker']: # this is tracker
        cursor = await metaDB.cursor()
        await cursor.execute('delete from filesystem where host_addr = ?', 
                        (config['ip'] + ':' + config['port'],))
        
        # record root physical path
        logging.info('loading ' + path)
        ctime_stamp = os.path.getctime(path)
        mtime_stamp = os.path.getmtime(path)
        ctime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ctime_stamp))
        mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime_stamp))
        await cursor.execute('''
            insert into filesystem
            (physical_path, category, ctime, mtime, size, host_addr, host_name)
            values  ('/', ?, ?, ?, ?, ?, ?)
            ''', 
            (os.path.isfile(path), ctime, mtime, os.path.getsize(path), 
            config['ip'] + ':' + config['port'], config['name'])
        )
        await metaDB.commit()

        # insert physical paths in this host
        await load_path(config['root'] + '/', cursor=cursor)
        await cursor.close()
    else:
        # ln root physical path
        header = make_header('ln ' + root_to_physical(path), os.path.getsize(path))
        reader, writer = await asyncio.open_connection(
                        config['tracker_ip'], config['tracker_port'])
        logging.debug(f'Send: {header!r}')
        writer.write(header.encode('utf-8'))
        await writer.drain()
        if await get_error(reader, writer):
            return
        logging.info('Link ' + path + ' successfully')
        writer.close()

        await load_path(config['root'] + '/', is_tracker=False)


async def init_db():
    global config, metaDB
    loop = asyncio.get_running_loop()
    metaDB = await aiosqlite3.connect('pns.sqlite3', loop=loop)
    cursor = await metaDB.cursor()
    # Both directory and file paths don't end with '/',
    # except logical root path
    await cursor.execute('''
    create table if not exists filesystem (
        logical_path  varchar,     -- all logical path is link
        physical_path varchar,     -- is null if logical_path is not linked
        category      int,         -- 0: path, 1: file, 2: link
        ctime         varchar,
        mtime         varchar,
        size          int,         -- zero for dir
        host_addr     varchar,     -- ip and port
        host_name     varchar
    )
    ''')
    
    # if the database is empty, 
    # this is the first time we start the tracker
    await cursor.execute('select count(*) from filesystem')
    if (await cursor.fetchone())[0] == 0:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # insert logical root
        await cursor.execute('''
            insert into filesystem
            (logical_path, category, ctime, mtime, size)
            values ('/', 2, ?, ?, 0)
            ''', (now, now)
        )
    await metaDB.commit()
    await cursor.close()


#------------------------------Callback & Utilities---------------------------#

# break physical_path into name/ip and relative path
# Judge whether location denote a name or ip by whether it contains '.'
# Judge whether relative path is in root by whether it starts with '/'
def parse_physical_path(physical_path):
    physical_path = physical_path.split('/', 3)
    location = physical_path[2]
    path = '/'
    if len(physical_path) > 3:
        path = physical_path[3]
        if path.find(':') == -1:  # if path is not like 'c:/dir/f'
            path = '/' + path
    return location, path

# 'path' is a logical path
async def parent_path_exists(path):
    cursor = await metaDB.cursor()
    await cursor.execute('select count(*) from filesystem where logical_path = ?',
                        (os.path.split(path)[0],))
    if (await cursor.fetchone())[0] == 0:
        return False
    return True

async def path_exists(path):
    cursor = await metaDB.cursor()
    await cursor.execute('select count(*) from filesystem where logical_path = ?',
                        (path,))
    if (await cursor.fetchone())[0] == 0:
        return False
    return True

# When initiate a physical root path for a new started daemon, 'dst' is None.
# 'src' is like '//ip:port/local/path'.
# 'host_name' is the peer's name.
# 'size' indicates the size of 'src'
async def echo_ln(src, dst, host_name, writer, size=0):
    global metaDB
    cursor = await metaDB.cursor()
    location, path = parse_physical_path(src)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if dst: # link a physical path to a logical path
        dst.rstrip('/')
        if not (await parent_path_exists(dst)):
            writer.write(b'E: 403 Parent Path Doesn\'t Exist\n\n')
            await writer.drain()
        elif await path_exists(dst):
            writer.write(b'E: 403 Path Already Exist\n\n')
            await writer.drain()
        else:
            await cursor.execute(
                'insert into filesystem values (?, ?, 2, ?, ?, ?, ?, ?)',
                (dst, path, now, now, size, location, host_name)
            )
            logging.info('link %s to %s successfully' % (src, dst))
    else: # Record a single physical path with no logical path
        # Judge whether the path is dir by whether it ends with '/'
        is_file = not path.endswith('/')
        if path != '/':
            path = path.rstrip('/')
        await cursor.execute('''
            insert into filesystem
            (physical_path, category, ctime, mtime, size, host_addr, host_name)
            values  (?, ?, ?, ?, ?, ?, ?)
            ''', (path, is_file, now, now, size, location, host_name))
        logging.info(f'update host {host_name!r}\'s path {path!r}')

    await cursor.close()
    await metaDB.commit()

    writer.write(b'E: 200 OK\n\n')
    await writer.drain()


# dst is a logical path, like '/dir/a/file', 
# or a physical path, like '//137.0.0.1/local/path'
#                      or  '//h2/local/path'
async def echo_ls(dst, writer):
    global metaDB
    cursor = await metaDB.cursor()
    file_list = []
    
    if dst == '//': # fetch all hosts' info
        await cursor.execute('''select distinct host_name, host_addr from filesystem
                        where host_name is not null and host_addr is not null''')
        results = await cursor.fetchall()
        if len(results) == 0: # should never happen
            writer.write(b'E: 500 No Host Detected\n\n')
            await writer.drain()
            return
        for record in results:
            item = {'name': record[0], 'addr': record[1]}
            file_list.append(item)
    elif dst.startswith('//'): # physical path
        dst = dst.rstrip('/')
        location, path = parse_physical_path(dst)
        if location.find('.') != -1: # if location denotes an 'ip:port'
            await cursor.execute('''
                select * from filesystem 
                where host_addr like ? and physical_path like ?
                    and not physical_path like ?
                order by physical_path asc
            ''', (location, path + '%%', path + '_%%/%%'))
        else: # if location denotes an name
            await cursor.execute('''
                select * from filesystem 
                where host_name = ? and physical_path like ?
                    and not physical_path like ?
                order by physical_path asc
            ''', (location, path + '%%', path + '_%%/%%'))
            
        results = await cursor.fetchall()
        if len(results) == 0:
            writer.write(b'E: 404 Path Not Found\n\n')
            await writer.drain()
            return
        for record in results:
            item = {
                'name' : record[1],
                'type' : 'f' if record[2] else 'd',
                'ctime': record[3],
                'mtime': record[4],
                'size' : record[5],
                'host' : record[6]  # ip and port
            }
            file_list.append(item)
    else: # logical path
        await cursor.execute('''
                select * from filesystem 
                where logical_path like ? and not logical_path like ?
                order by logical_path asc
                ''', (dst + '%%', dst + '_%%/%%'))
        results = await cursor.fetchall()
        if len(results) == 0:
            writer.write(b'E: 404 Path Not Found\n\n')
            await writer.drain()
            return
        for record in results:
            item = {
                'name' : record[0],
                'type' : record[1], # the physical path for this logical path
                'ctime': record[3],
                'mtime': record[4],
                'size' : record[5],
                'host' : record[6]  # ip and port
            }
            file_list.append(item)
    await cursor.close()
    await metaDB.commit()
    data = b'E: 200 OK\n\n' + json.dumps(file_list).encode('utf-8')
    logging.debug(data)
    writer.write(data) # need to add 'L' field in header?
    writer.write_eof()
    await writer.drain()


# dst is logical path, like '/logical/path'
async def echo_md(dst, writer):
    global metaDB
    cursor = await metaDB.cursor()

    dst.rstrip('/')
    if not (await parent_path_exists(dst)):
        writer.write(b'E: 403 Parent Path Doesn\'t Exist\n\n')
        await writer.drain()
    elif await path_exists(dst):
        writer.write(b'E: 403 Path Already Exist\n\n')
        await writer.drain()
    else:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await cursor.execute('''
            insert into filesystem
            (logical_path, category, ctime, mtime, size)
            values (?, 2, ?, ?, 0)
            ''', (dst, now, now)
        )
        await metaDB.commit()
        writer.write(b'E: 200 OK\n\n')
        await writer.drain()
    await cursor.close()


# If dst is logical path, like '/logical/path', 
# then removing it just disconnects its link with corresponding physical path
# If dst is physical path, like '//hostnameOrSock/local/path', 
# then removing it just erases the record in db and doesn't actually delete it.
async def echo_rm(dst, writer):
    global metaDB
    cursor = await metaDB.cursor()

    dst.rstrip('/')
    if dst[1] != '/': # logical path
        await cursor.execute('select physical_path from filesystem where logical_path = ?',
                        (dst,))
        result = await cursor.fetchone()
        if not result:
            writer.write(b'E: 404 Path Not Found\n\n')
            await writer.drain()
            return
        physical_path = result[0]
        if not physical_path or not physical_path.startswith('/'):
            await cursor.execute('''
                    delete from filesystem 
                    where logical_path like ? or logical_path = ?
                    ''', (dst + '/%%', dst))
            logging.info('delete logical path \'%s\''% dst)
        else:
            await cursor.execute('''
                update filesystem set logical_path = null
                where logical_path = ? 
            ''', (dst,))
            logging.info('disconnect logical path \'%s\' with its physical path' % dst)
    else: # physical path
        location, dst_path = parse_physical_path(dst)
        if location.find('.') == -1: # location is a ip:port
            await cursor.execute('''
                    delete from filesystem 
                    where physical_path = ? and host_name = ?
                ''', (dst_path, location))
        else:  # location is a hostname
            await cursor.execute('''
                    delete from filesystem 
                    where physical_path = ? and host_addr = ?
                ''', (dst_path, location))
        logging.info(f'delete a physical path {dst_path!r} from {location!r}')
    
    await metaDB.commit()
    await cursor.close()
    writer.write(b'E: 200 OK\n\n')
    await writer.drain()


# src and dst should both be physical path
# Either src or dst must be this host, like '//127.0.0.1:8080/path'
# which determine whether this host sends or receives a file
# support only single file transfer
async def echo_cp(src, dst, reader, writer, size=0):
    src_addr, src_path = parse_physical_path(src)
    dst_addr, dst_path = parse_physical_path(dst)
    
    if src_addr.split(':')[0] == config['ip']: # this is sending side
        if src_path.find(':') == -1:  # the file may be in root directory
            src_path = config['root'] + src_path
        if os.path.isfile(src_path):  # also return false if path doesn't exist
            loop = asyncio.get_running_loop()
            # 'dst_path' is not important here, as we send file through 'tr'
            await send_file(src_path, dst_path, writer, loop)
            if await get_error(reader, writer):
                return
            logging.info('Send file successfully!')
        else:
            writer.write(b'E: 404 File Not Found\n\n')
            await writer.drain()

    elif dst_addr.split(':')[0] == config['ip']: # this is receiving side
        # allow only copying to root directory
        dst_path = config['root'] + dst_path
        if not os.path.exists(dst_path):  # if we are not covering a existing file
             with open(dst_path, 'wb') as f:
                if size > 0:
                    origin_size = size
                    while size >= 4096:
                        f.write(await reader.read(4096))
                        size -= 4096
                    f.write(await reader.read(size))
                    writer.write(b'E: 200 OK\n\n')
                    await writer.drain()
                    # record new copied file in db
                    if config['istracker']:
                        cursor = await metaDB.cursor()
                        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        path = root_to_relative(dst_path)
                        await cursor.execute('''
                            insert into filesystem
                            (physical_path, category, ctime, mtime, size, host_addr, host_name)
                            values  (?, 1, ?, ?, ?, ?, ?)
                            ''', 
                            (path, now, now, origin_size, 
                            config['ip'] + ':' + config['port'], config['name'])
                        )
                        await metaDB.commit()
                        await cursor.close()
                        logging.info('update host %s\'s path %s' % (config['name'], path))
                    else:
                        await load_path(dst_path, is_tracker=False)
                else:
                    writer.write(b'E: 400 No Length Field\n\n')
                    await writer.drain()
        else: 
            writer.write(b'E: 403 File Already Exists\n\n')
            await writer.drain()


# This must be the sending side
# src is like '//hostsock/path'
async def echo_mv(src, dst, reader, writer, size=0):
    src_addr, relative_path = parse_physical_path(src)
    src_path = config['root'] + relative_path
    # allow only moving from root directory
    if not os.path.exists(src_path):
        writer.write(b'E: 404 File Not Found\n\n')
        await writer.drain()
        return
    await echo_cp(src, dst, reader, writer, size)
    os.remove(src_path)
    if config['istracker']:
        cursor = await metaDB.cursor()
        await cursor.execute('''
            delete from filesystem 
            where physical_path = ? and host_addr = ?
        ''', (relative_path, src_addr))
        await metaDB.commit()
        await cursor.close()
        logging.info(f'delete a physical path {relative_path!r} from {src_addr!r}')
    else:
        await rm(src)
    logging.info(f'Remove {src_path!r}.')


async def echo_illegal_command(writer):
    logging.warning('Reciive illegal command')
    writer.write(b'E: 400 Illegal Command\n\n')
    await writer.drain()


async def echo_request(reader, writer):
    data = await reader.readuntil(b'\n\n')
    header = parse_header(data.decode('utf-8'))

    # check required fields
    if not 'V' in header:
        writer.write(b'E: 400 No Version Field\n\n')
        await writer.drain()
        return
    if not 'A' in header:
        writer.write(b'E: 400 No Authorization Field\n\n')
        await writer.drain()
        return
    if not 'C' in header:
        writer.write(b'E: 400 No Command Field\n\n')
        await writer.drain()
        return

    # check authorization
    sha1 = hashlib.sha1()
    sha1.update(config['secret'].encode('utf-8'))
    sha1.update(header['C'].encode('utf-8'))
    if sha1.hexdigest() != header['A']: # wrong password
        writer.write(b'E: 401 Unauthorized\n\n')
        await writer.drain()
        return

    host_name, ver = header['V'].split(' ', 1)
    # If we receive a host's heartbeat, update 'daemons'
    if ver == 'HB':
        now = time.time()
        daemons[host_name] = now
        logging.info(f'Update {host_name!r}\'s timestamp: {now!r}')
        

    # handle authorized request
    cmd = header['C'].split(' ')
    if cmd[0] == 'ln':
        if len(cmd) < 2:
            await echo_illegal_command(writer)
            return
        elif len(cmd) == 2: # if we are link just a physical path
            cmd.append(None)
        if 'L' in header:
            await echo_ln(cmd[1], cmd[2], host_name, writer, int(header['L']))
        else:
            await echo_ln(cmd[1], cmd[2], host_name, writer)
    elif cmd[0] == 'ls':
        if len(cmd) < 2:
            await echo_illegal_command(writer)
            return
        await echo_ls(cmd[1], writer)
    elif cmd[0] == 'md':
        if len(cmd) < 2:
            await echo_illegal_command(writer)
            return
        await echo_md(cmd[1], writer)
    elif cmd[0] == 'rm':
        if len(cmd) < 2:
            await echo_illegal_command(writer)
            return
        await echo_rm(cmd[1], writer)
    elif cmd[0] == 'cp':
        if len(cmd) < 3:
            await echo_illegal_command(writer)
            return
        if 'L' in header:
            await echo_cp(cmd[1], cmd[2], reader, writer, int(header['L']))
        else:
            await echo_cp(cmd[1], cmd[2], reader, writer)
    elif cmd[0] == 'mv':
        if len(cmd) < 3:
            await echo_illegal_command(writer)
            return
        if 'L' in header:
            await echo_mv(cmd[1], cmd[2], reader, writer, int(header['L']))
        else:
            await echo_mv(cmd[1], cmd[2], reader, writer)
    else:
        await echo_illegal_command(writer)
        return

    logging.info('Continue serving...')


async def heartbeat():
    header = make_header('ls //', is_heartbeat=True).encode('utf-8')
    while True:
        await asyncio.sleep(1)
        reader, writer = await asyncio.open_connection(
                config['tracker_ip'], config['tracker_port'])
        writer.write(header)
        await writer.drain()
        logging.info(f'Send heartbeat')
        if await get_error(reader, writer):
            return
        
        # get living daemons
        data = await reader.read()
        response = json.loads(data)
        daemons = [daemon['name'] for daemon in response]
        now = time.time()
        logging.info(f'Living daemons: {daemons!r}. time:{now!r}')


async def listen_heartbeat():
    global daemons
    while True:
        await asyncio.sleep(3)
        host_names = list(daemons.keys()) # make a copy of keys
        now = time.time()
        for name in host_names:
            if now - daemons[name] > 3:
                del daemons[name]
                # remove its records in database
                cursor = await metaDB.cursor()
                await cursor.execute('delete from filesystem where host_name=?', 
                                    (name,))
                await metaDB.commit()
                await cursor.close()
                logging.info('Unmount host %s'% name)


async def start_daemon():
    # if we're starting tracker, initiate meta database
    if config['istracker']:
        await init_db()
    await update_db()

    # send heartbeat to tracker periodically
    if not config['istracker']:
        asyncio.create_task(heartbeat())
    else:
        asyncio.create_task(listen_heartbeat())
    
    server = await asyncio.start_server(
        echo_request, config['ip'], config['port'])
    addr = server.sockets[0].getsockname()
    logging.info(f'Serving on {addr}')
    async with server:
        await server.serve_forever()

    # if this is tracker, close database
    if config['istracker']:
        # close database
        global metaDB
        await metaDB.commit()
        await metaDB.close()


##############################################################################
#------------------------------Shell Side------------------------------------#

# judge if the path is in this host
def path_in_this_host(path):
    # os.path.exists(path) ?
    if path[0] != '/': # path is something like 'd:/pns/root/file'
        return True
    if path[1] != '/': # path is a logical path
        return False
    global config
    location = path.split('/', 3)[2]
    if config['ip'] == location or config['name'] == location:
        return True
    return False

# input path to local path
def extract_local_path_from(path):
    if path[0] != '/': # path is something like 'd:/pns/root/file'
        return path
    global config      # path is something like '//nameOrIp/pns/root/file'
    relative_path = path.split('/', 3)[3] # not check if path.split('/', 3) has index 3
    return config['root'] + '/' + relative_path


async def ln(src, dst):
    # if not dst: # link a single physical path
    if not os.path.exists(src):
        logging.warning('src does not exist')
        return
    
    reader, writer = await asyncio.open_connection(
                    config['tracker_ip'], config['tracker_port'])
    cmd = 'ln ' + root_to_physical(src.rstrip('/'), in_default_root=False) + ' ' + dst.rstrip('/')
    header = make_header(cmd, os.path.getsize(src))
    logging.debug(f'Send: {header!r}')
    writer.write(header.encode('utf-8'))
    await writer.drain()
    if await get_error(reader, writer):
        return
    logging.info('Link ' + src + ' successfully')
    writer.close()
    
    if os.path.isdir(src):
        await load_path(src.rstrip('/') + '/', logical_root=dst.rstrip('/') + '/', 
                in_default_root=False, is_tracker=False)


async def ls(dst):
    reader, writer = await asyncio.open_connection(
            config['tracker_ip'], config['tracker_port'])
    header = make_header('ls ' + dst)
    logging.debug(f'Send: {header!r}')
    writer.write(header.encode('utf-8'))
    await writer.drain()

    if await get_error(reader, writer):
        return
    
    # get json data
    data = await reader.read()
    response = json.loads(data)
    logging.info(response)
    writer.close()


async def md(dst):
    reader, writer = await asyncio.open_connection(
            config['tracker_ip'], config['tracker_port'])
    header = make_header('md ' + dst)
    logging.debug(f'Send: {header!r}')
    writer.write(header.encode('utf-8'))
    await writer.drain()
    
    if await get_error(reader, writer):
        return
    logging.info('Make directory successfully')
    writer.close()


async def rm(dst):
    reader, writer = await asyncio.open_connection(
            config['tracker_ip'], config['tracker_port'])
    header = make_header('rm ' + dst)
    logging.debug(f'Send: {header!r}')
    writer.write(header.encode('utf-8'))
    await writer.drain()
    
    if await get_error(reader, writer):
        return
    logging.info('Remove successfully')
    writer.close()


# At least one of 'src' and 'dst' should be in this host.
# The one which is in other host is something like '//hostname/path' or '/logical/path'
async def cp(src, dst, delete_src=False):
    src_is_here = path_in_this_host(src)
    dst_is_here = path_in_this_host(dst)
    if not src_is_here and not dst_is_here:
        logging.warning("Either src or dst should be in this host!")
    elif src_is_here and dst_is_here: 
        # use local filesystem
        src_path = extract_local_path_from(src)
        if not os.path.exists(src_path):
            logging.warning('src doesn\'t exist!')
            return
        dst_path = extract_local_path_from(dst)
        if delete_src:
            shutil.move(src_path, dst_path)
        else:
            shutil.copyfile(src_path, dst_path)

    elif src_is_here: # this is sending side
        src_path = extract_local_path_from(src)
        if not os.path.exists(src_path):
            logging.warning('src doesn\'t exist!')
            return
                
        # try to get dst ip and port
        reader, writer = await asyncio.open_connection(
            config['tracker_ip'], config['tracker_port'])
        parent = dst[0: dst.rfind('/')]
        header = make_header('ls ' + parent)
        logging.debug(f'Send: {header!r}')
        writer.write(header.encode('utf-8'))
        await writer.drain()
        if await get_error(reader, writer):
            return
        # get response
        data = await reader.read()
        response = json.loads(data)
        dst_sock = response[0]['host'].split(':')
        if dst[1] != '/': # dst is logical path
            file_name = dst[dst.rfind('/'): ]
            relative_path = response[0]['type'] + '/' + file_name
        else:             # dst is physical path
            relative_path = dst.split('/', 3)[3]
        writer.close()

        # send file to dst
        reader, writer = await asyncio.open_connection(dst_sock[0], dst_sock[1])
        loop = asyncio.get_running_loop()
        dst_path = '//' + ':'.join(dst_sock) + '/' + relative_path
        await send_file(src_path, dst_path, writer, loop)
        if await get_error(reader, writer):
            writer.close()
            return
        if delete_src:
            os.remove(src_path)
            await rm(src)
        logging.info('Send file successfully!')
        writer.close()

    else: # dst_is_here
        dst_path = extract_local_path_from(dst)
        # try to get src ip and port
        reader, writer = await asyncio.open_connection(
            config['tracker_ip'], config['tracker_port'])
        header = make_header('ls ' + src)
        logging.debug(f'Send: {header!r}')
        writer.write(header.encode('utf-8'))
        await writer.drain()
        if await get_error(reader, writer):
            writer.close()
            return
        # get response
        data = await reader.read()
        response = json.loads(data)
        src_sock = response[0]['host'].split(':')
        if src[1] != '/': # src is logical path
            relative_path = response[0]['type'].lstrip('/')
        else:
            relative_path = src.split('/', 3)[3]
        writer.close()

        # send a header to let src host start sending file
        reader, writer = await asyncio.open_connection(src_sock[0], src_sock[1])
        loop = asyncio.get_running_loop()
        src_path = '//' + ':'.join(src_sock) + '/' + relative_path
        if delete_src:
            header = make_header('mv ' + src_path + ' ' + dst_path)
        else:
            header = make_header('cp ' + src_path + ' ' + dst_path)
        logging.debug(f'Send: {header!r}')
        writer.write(header.encode('utf-8'))
        await writer.drain()

        # receive file from src
        rec_header = await reader.readuntil(b'\n\n')
        header = parse_header(rec_header.decode('utf-8'))
        if 'E' in header and header['E'].split(' ', 1)[0] != '200':
            logging.error(header['E'])
            writer.close()
            return
        if not 'L' in header:
            logging.warning('No length field detcted.')
            writer.close()
            return
        size = int(header['L'])
        if size < 0:
            logging.warning('Illegal length field.')
            writer.close()
            return
        with open(dst_path, 'wb') as f:
            while size >= 4096:
                f.write(await reader.read(4096))
                size -= 4096
            f.write(await reader.read(size))
            writer.write(b'E: 200 OK\n\n')
            await writer.drain()
        logging.info(f'Receive file {src_path!r} successfully')
        writer.close()

        # if dst is in root, ln it in tracker's db
        if dst_path.find(config['root']) != -1:
            await load_path(dst_path, is_tracker=False)


async def mv(src, dst):
    await cp(src, dst, delete_src=True)


async def do_command(cmd, *args):
    if cmd == 'cp':
        await cp(*args)
    elif cmd == 'ln':
        await ln(*args)
    elif cmd == 'ls':
        await ls(*args)
    elif cmd == 'md':
        await md(*args)
    elif cmd == 'mv':
        await mv(*args)
    elif cmd == 'rm':
        await rm(*args)
    else:
        logging.warning('Unknown Command')


#-----------------------------------Command Parser------------------------------------#

def main():
    parser = argparse.ArgumentParser(description='Personal Network Storage')
    parser.add_argument('-m', '--mode', dest='mode',
                        help='mode can be shell or daemon')
    parser.add_argument('-c', '--config', dest='config',
                        help='the config file (*.yml)')
    parser.add_argument('cmd', nargs='*',
                        help='(Available in shell mode) the command to be executed')

    args = parser.parse_args()

    if args.config:
        parse_config(args.config)
    else:
        logging.warning('No config file detected!')

    if args.mode == 'shell':
        if not args.cmd:
            logging.info('Please enter a command')
        else:
            asyncio.run(do_command(*(args.cmd)))
    elif args.mode == 'daemon':
        logging.info('Starting daemon...')
        # for Windows, use iocp
        # if sys.platform == 'win32':
        #     asyncio.set_event_loop(asyncio.ProactorEventLoop())\
        asyncio.run(start_daemon()) 
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
