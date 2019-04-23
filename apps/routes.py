from flask import request, Response, Blueprint
from media import get_collection
from bson import json_util, ObjectId
from .errors import bad_request
from media.video import get_video_editor_tool
from media.utils import create_file_name, validate_json
from flask import current_app as app
from werkzeug.datastructures import FileStorage
import io

bp = Blueprint('projects', __name__)

SCHEMA_UPLOAD = {'media': {'type': 'binary'}}


@bp.route('/projects', methods=['POST'])
def create_video_editor():
    """
        Api put a file into storage video server
        content-type: multipart/form-data
        payload:
            {'media': {'type': 'file'}}

        response: http 200
        {
            "filename": "fa5079a38e0a4197864aa2ccb07f3bea.mp4",
            "metadata": null,
            "client_info": "PostmanRuntime/7.6.0",
            "version": 1,
            "processing": false,
            "parent": null,
            "thumbnails": {},
            "_id": {
                "$oid": "5cbd5acfe24f6045607e51aa"
            }
        }
    :return:
    """
    if request.method == 'POST':
        files = request.files
        user_agent = request.headers.environ['HTTP_USER_AGENT']
        return create_video(files, user_agent)


@bp.route('/projects/<path:video_id>', methods=['GET', 'POST', 'PUT', 'DELETE'])
def process_video_editor(video_id):
    """Keep previous url for backward compatibility"""
    if request.method == 'GET':
        return get_video(video_id)
    if request.method == 'PUT':
        return update_video(video_id, request.form)
    if request.method == 'POST':
        return update_video(video_id, request.form)
    if request.method == 'DELETE':
        return delete_video(video_id)


def delete_video(video_id):
    video = get_collection('media')
    video.remove({'_id': format_id(video_id)})
    return 'delete successfully'


def update_video(video_id, updates):
    return 'update successfully'


def create_video(files, agent):
    """Validate data, then save video to storage and create records to databases"""
    #: validate incoming data is a file
    if 'media' not in files or not isinstance(files.get('media'), FileStorage):
        return bad_request("file can not found in 'media'")

    #: validate the user agent must be in a list support
    client_name = agent.split('/')[0]
    if client_name.lower() not in app.config.get('AGENT_ALLOW'):
        return bad_request("client is not allow to edit")

    video_editor = get_video_editor_tool('ffmpeg')
    file = files.get('media')
    file_stream = file.stream.read()
    metadata = video_editor.get_meta(file_stream)
    #: validate codec must be support
    if metadata.get('codec_name') not in app.config.get('CODEC_SUPPORT'):
        return bad_request("codec is not support")

    ext = file.filename.split('.')[1]
    file_name = create_file_name(ext)
    #: put file into storage
    doc = app.fs.put(None, file_stream, file_name, metadata=metadata, client_info=agent)
    return Response(json_util.dumps(doc), status=201, mimetype='application/json')


def get_video(video_id):
    """Get data media"""
    media = get_collection('media')
    items = list(media.find({'_id': format_id(video_id)}))
    for item in items:
        item['_id'] = str(item['_id'])
    return Response(json_util.dumps(items), status=200, mimetype='application/json')


def format_id(_id):
    try:
        return ObjectId(_id)
    except Exception as ex:
        return None
