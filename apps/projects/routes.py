import logging
import os
import re
from datetime import datetime

from bson import json_util
from flask import abort, request, make_response
from flask import current_app as app
from flask.views import MethodView

from lib.errors import bad_request, forbidden, not_found
from lib.utils import create_file_name, format_id, json_response, paginate
from lib.validator import Validator
from lib.video_editor import get_video_editor
from . import bp
from .tasks import task_get_list_thumbnails, task_edit_video

logger = logging.getLogger(__name__)


def check_user_agent():
    user_agent = request.headers.environ.get('HTTP_USER_AGENT')

    client_name = user_agent.split('/')[0]
    if client_name.lower() not in app.config.get('AGENT_ALLOW'):
        abort(bad_request("client is not allow to edit"))
    return user_agent


def check_request_schema_validity(request_schema, schema):
    validator = Validator(schema)
    if not validator.validate(request_schema):
        abort(bad_request(validator.errors))


class UploadProject(MethodView):
    SCHEMA_UPLOAD = {
        'file': {
            'type': 'filestorage',
            'required': True
        }
    }

    def post(self):
        """
        Create new project record in DB and save file into file storage
        ---
        consumes:
          - multipart/form-data
        parameters:
        - in: formData
          name: file
          type: file
          description: file object to upload
        responses:
          201:
            description: CREATED
            schema:
              type: object
              properties:
                filename:
                  type: string
                  example: fa5079a38e0a4197864aa2ccb07f3bea.mp4
                folder:
                  type: string
                  example: 2019/5
                metadata:
                  type: object
                  properties:
                    codec_name:
                      type: string
                      example: h264
                    codec_long_name:
                      type: string
                      example: H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10
                    width:
                      type: string
                      example: 640
                    height:
                      type: string
                      example: 360
                    duration:
                      type: string
                      example: 300.014000
                    bit_rate:
                      type: string
                      example: 287654
                    nb_frames:
                      type: string
                      example: 7654
                    r_frame_rate:
                      type: string
                      example: 24/1
                    format_name:
                      type: string
                      example: mov,mp4,m4a,3gp,3g2,mj2
                    size:
                      type: string
                      example: 14567890
                mime_type:
                  type: string
                  example: video/mp4
                create_time:
                  type: object
                  properties:
                    $date:
                      type: integer
                      example: 1556853105063
                original_filename:
                  type: string
                  example: video.mp4
                client_info:
                  type: string
                  example: Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:66.0) Gecko/20100101 Firefox/66.0
                version:
                  type: integer
                  example: 1
                parent:
                  type: object
                  example: {}
                processing:
                  type: boolean
                  example: False
                thumbnails:
                  type: object
                  example: {}
                _id:
                  type: object
                  properties:
                    $oid:
                      type: string
                      example: 5cbd5acfe24f6045607e51aa
        """

        # validate request
        if 'file' not in request.files:
            # to avoid TypeError: cannot serialize '_io.BufferedRandom' error
            return bad_request({"file": ["required field"]})

        # validate user-agent
        user_agent = check_user_agent()

        # validate request
        check_request_schema_validity(request.files, self.SCHEMA_UPLOAD)

        # validate codec
        video_editor = get_video_editor()
        file = request.files['file']
        file_stream = file.stream.read()
        metadata = video_editor.get_meta(file_stream)
        codec_name = metadata.get('codec_name')
        if codec_name not in app.config.get('CODEC_SUPPORT'):
            return bad_request("Codec: {} is not supported.".format(codec_name))

        # generate file path
        file_name = create_file_name(ext=file.filename.split('.')[1])
        utcnow = datetime.utcnow()
        folder = f'{utcnow.year}/{utcnow.month}/{utcnow.day}'
        file_path = os.path.join(app.config.get('FS_MEDIA_STORAGE_PATH'), folder, file_name)

        # put file stream into storage
        if app.fs.put(file_stream, file_path=file_path):
            try:
                # add record to database
                doc = {
                    'filename': file_name,
                    'folder': folder,
                    'metadata': metadata,
                    'create_date': utcnow,
                    'mime_type': file.mimetype,
                    'version': 1,
                    'processing': False,
                    'parent': None,
                    'thumbnails': {},
                    'client_info': user_agent,
                    'original_filename': file.filename,
                    'preview_thumbnail': None,
                    'url': None
                }
                app.mongo.db.projects.insert_one(doc)
                # create url for preview video
                doc['url'] = app.fs.url_for_media(doc.get('_id'))
                app.mongo.db.projects.update_one(
                    {'_id': doc['_id']},
                    {'$set': {
                        'url': doc['url'],

                    }}
                )

                activity = {
                    "action": "UPLOAD",
                    "file_id": doc.get('_id'),
                    "payload": {"file": doc.get(file.filename)},
                    "create_date": utcnow
                }
                app.mongo.db.activity.insert_one(activity)
            except Exception as ex:
                # remove file from storage
                app.fs.delete(file_name)
                return forbidden("Can not insert a record to database: {}".format(ex))
        else:
            return forbidden("Can not store file")
        return json_response(doc, status=201)

    def get(self):
        """
        Get list of projects in DB
        ---
        parameters:
        - name: offset
          in: query
          type: integer
          description: Page number
        responses:
          200:
            description: OK
            schema:
              type: object
              properties:
                offset:
                  type: integer
                  example: 1
                size:
                  type: integer
                  example: 14
                max_size:
                  type: integer
                  example: 50
                items:
                  type: array
                  items:
                    type: object
                    properties:
                      filename:
                        type: string
                        example: fa5079a38e0a4197864aa2ccb07f3bea.mp4
                      folder:
                        type: string
                        example: 2019/5
                      metadata:
                        type: object
                        properties:
                          codec_name:
                            type: string
                            example: h264
                          codec_long_name:
                            type: string
                            example: H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10
                          width:
                            type: string
                            example: 640
                          height:
                            type: string
                            example: 360
                          duration:
                            type: string
                            example: 300.014000
                          bit_rate:
                            type: string
                            example: 287654
                          nb_frames:
                            type: string
                            example: 7654
                          r_frame_rate:
                            type: string
                            example: 24/1
                          format_name:
                            type: string
                            example: mov,mp4,m4a,3gp,3g2,mj2
                          size:
                            type: string
                            example: 14567890
                      mime_type:
                        type: string
                        example: video/mp4
                      create_time:
                        type: object
                        properties:
                          $date:
                            type: integer
                            example: 1556853105063
                      original_filename:
                        type: string
                        example: video.mp4
                      client_info:
                        type: string
                        example: Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:66.0) Gecko/20100101 Firefox/66.0
                      version:
                        type: integer
                        example: 1
                      parent:
                        type: object
                        example: {}
                      processing:
                        type: boolean
                        example: False
                      thumbnails:
                        type: object
                        example: {}
                      _id:
                        type: object
                        properties:
                          $oid:
                            type: string
                            example: 5cbd5acfe24f6045607e51aa
        """
        offset = request.args.get('offset', 0, type=int)
        max_size = int(app.config.get('ITEMS_PER_PAGE', 25))
        # get all projects
        docs = list(app.mongo.db.projects.find())
        list_pages = list(paginate(docs, max_size))
        if offset >= len(list_pages):
            offset = len(list_pages) - 1
        res = {
            'items': list_pages[offset],
            'offset': offset,
            'size': len(list_pages[offset]),
            'max_size': max_size
        }
        return json_response(res)


class RetrieveEditDestroyProject(MethodView):
    SCHEMA_EDIT = {
        'capture': {
            'type': 'dict',
            'required': False,
            'empty': True,
        },
        'cut': {
            'type': 'dict',
            'required': False,
            'empty': True,
            'schema': {
                'start': {'type': 'integer', 'required': True},
                'end': {'type': 'integer', 'required': True},
            },
        },
        'rotate': {
            'type': 'dict',
            'required': False,
            'empty': True,
            'schema': {
                'degree': {'type': 'integer', 'required': True}
            },
        },
        'quality': {
            'type': 'dict',
            'required': False,
            'empty': True,
        },
        'crop': {
            'type': 'dict',
            'required': False,
            'empty': True,
            'schema': {
                'width': {'type': 'integer', 'required': True},
                'height': {'type': 'integer', 'required': True},
                'x': {'type': 'integer', 'required': True},
                'y': {'type': 'integer', 'required': True}
            }
        }
    }

    def get(self, project_id):
        """
        Retrieve project details
        ---
        parameters:
            - name: project_id
              in: path
              type: string
              required: true
              description: Unique project id
        responses:
          200:
            description: OK
            schema:
              type: object
              properties:
                filename:
                  type: string
                  example: fa5079a38e0a4197864aa2ccb07f3bea.mp4
                folder:
                  type: string
                  example: 2019/5
                metadata:
                  type: object
                  properties:
                    codec_name:
                      type: string
                      example: h264
                    codec_long_name:
                      type: string
                      example: H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10
                    width:
                      type: string
                      example: 640
                    height:
                      type: string
                      example: 360
                    duration:
                      type: string
                      example: 300.014000
                    bit_rate:
                      type: string
                      example: 287654
                    nb_frames:
                      type: string
                      example: 7654
                    r_frame_rate:
                      type: string
                      example: 24/1
                    format_name:
                      type: string
                      example: mov,mp4,m4a,3gp,3g2,mj2
                    size:
                      type: string
                      example: 14567890
                mime_type:
                  type: string
                  example: video/mp4
                create_time:
                  type: object
                  properties:
                    $date:
                      type: integer
                      example: 1556853105063
                original_filename:
                  type: string
                  example: video.mp4
                client_info:
                  type: string
                  example: Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:66.0) Gecko/20100101 Firefox/66.0
                version:
                  type: integer
                  example: 1
                parent:
                  type: object
                  example: {}
                processing:
                  type: boolean
                  example: False
                thumbnails:
                  type: object
                  example: {}
                _id:
                  type: object
                  properties:
                    $oid:
                      type: string
                      example: 5cbd5acfe24f6045607e51aa
        """

        doc = app.mongo.db.projects.find_one({'_id': format_id(project_id)})
        if not doc:
            return not_found("Project with id: {} was not found.".format(project_id))
        return json_response(doc)

    def put(self, project_id):
        """
        Edit video. This method does not create a new project.
        ---
        consumes:
        - application/json
        parameters:
        - in: path
          name: project_id
          type: string
          required: True
          description: Unique project id
        - in: body
          name: action
          description: Actions want to apply to the video
          required: True
          schema:
            type: object
            properties:
              cut:
                type: object
                properties:
                  start:
                    type: integer
                    example: 5
                  end:
                    type: integer
                    example: 10
              crop:
                type: object
                properties:
                  width:
                    type: integer
                    example: 480
                  height:
                    type: integer
                    example: 360
                  x:
                    type: integer
                    example: 10
                  y:
                    type: integer
                    example: 10
              rotate:
                type: object
                properties:
                  degree:
                    type: integer
                    example: 90
        responses:
          200:
            description: OK
            schema:
              type: object
              properties:
                filename:
                  type: string
                  example: fa5079a38e0a4197864aa2ccb07f3bea.mp4
                folder:
                  type: string
                  example: 2019/5
                metadata:
                  type: object
                  properties:
                    codec_name:
                      type: string
                      example: h264
                    codec_long_name:
                      type: string
                      example: H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10
                    width:
                      type: string
                      example: 640
                    height:
                      type: string
                      example: 360
                    duration:
                      type: string
                      example: 300.014000
                    bit_rate:
                      type: string
                      example: 287654
                    nb_frames:
                      type: string
                      example: 7654
                    r_frame_rate:
                      type: string
                      example: 24/1
                    format_name:
                      type: string
                      example: mov,mp4,m4a,3gp,3g2,mj2
                    size:
                      type: string
                      example: 14567890
                mime_type:
                  type: string
                  example: video/mp4
                create_time:
                  type: object
                  properties:
                    $date:
                      type: integer
                      example: 1556853105063
                original_filename:
                  type: string
                  example: video.mp4
                client_info:
                  type: string
                  example: Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:66.0) Gecko/20100101 Firefox/66.0
                version:
                  type: integer
                  example: 1
                parent:
                  type: object
                  example: {}
                processing:
                  type: boolean
                  example: False
                thumbnails:
                  type: object
                  example: {}
                _id:
                  type: object
                  properties:
                    $oid:
                      type: string
                      example: 5cbd5acfe24f6045607e51aa
        """
        # validate user-agent
        check_user_agent()
        # validate request
        check_request_schema_validity(request.get_json(), self.SCHEMA_EDIT)

        doc = app.mongo.db.projects.find_one({'_id': format_id(project_id)})
        if not doc:
            return not_found("Project with id: {} was not found.".format(project_id))
        if doc.get('processing') is True:
            return forbidden('this video is still processing, please wait.')
        if not doc.get('version') >= 2:
            return bad_request("Only PUT action for edited video version 2")
        file_path = os.path.join(app.config.get('FS_MEDIA_STORAGE_PATH'), doc.get('folder'), doc.get('filename'))
        # Update processing is True when begin edit video
        app.mongo.db.projects.update_one(
            {'_id': doc['_id']},
            {'$set': {
                'processing': True,
            }}
        )
        doc['processing'] = True
        task_edit_video.delay(file_path, json_util.dumps(doc), request.get_json())
        activity = {
            "action": "EDIT PUT",
            "project_id": doc.get('_id'),
            "payload": request.get_json(),
            "create_date": datetime.utcnow()
        }
        app.mongo.db.activity.insert_one(activity)
        return json_response(doc)

    def post(self, project_id):
        """
        Edit video. This method creates a new project.
        ---
        consumes:
        - application/json
        parameters:
        - in: path
          name: project_id
          type: string
          required: True
          description: Unique project id
        - in: body
          name: action
          description: Actions want to apply to the video
          required: True
          schema:
            type: object
            properties:
              cut:
                type: object
                properties:
                  start:
                    type: integer
                    example: 5
                  end:
                    type: integer
                    example: 10
              crop:
                type: object
                properties:
                  width:
                    type: integer
                    example: 480
                  height:
                    type: integer
                    example: 360
                  x:
                    type: integer
                    example: 10
                  y:
                    type: integer
                    example: 10
              rotate:
                type: object
                properties:
                  degree:
                    type: integer
                    example: 90
        responses:
          200:
            description: OK
            schema:
              type: object
              properties:
                filename:
                  type: string
                  example: fa5079a38e0a4197864aa2ccb07f3bea_v2.mp4
                folder:
                  type: string
                  example: 2019/5
                metadata:
                  type: object
                  example: {}
                mime_type:
                  type: string
                  example: video/mp4
                create_time:
                  type: object
                  properties:
                    $date:
                      type: integer
                      example: 1556853105063
                original_filename:
                  type: string
                  example: video.mp4
                client_info:
                  type: string
                  example: Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:66.0) Gecko/20100101 Firefox/66.0
                version:
                  type: integer
                  example: 2
                parent:
                  type: object
                  parameters:
                    _id:
                      type: object
                      parameters:
                        $oid:
                          type: string
                          example: 5ccbc4104dfd9b8fa153d60e
                processing:
                  type: boolean
                  example: False
                thumbnails:
                  type: object
                  example: {}
                _id:
                  type: object
                  properties:
                    $oid:
                      type: string
                      example: 5cbd5acfe24f6045607e51aa
        """
        # validate user-agent
        user_agent = check_user_agent()
        # validate request
        check_request_schema_validity(request.get_json(), self.SCHEMA_EDIT)

        doc = app.mongo.db.projects.find_one({'_id': format_id(project_id)})
        if not doc:
            return not_found("Project with id: {} was not found.".format(project_id))

        filename, ext = os.path.splitext(doc['filename'])
        if doc.get('version') >= 2:
            return bad_request("Only POST action for original video version 1")
        version = doc.get('version', 1) + 1
        new_file_name = f'{filename}_v{version}{ext}'
        new_doc = {
            'filename': new_file_name,
            'folder': doc.get('folder'),
            'metadata': None,
            'client_info': user_agent,
            'version': version,
            'processing': True,
            'mime_type': doc.get('mime_type'),
            'parent': {
                '_id': doc.get('_id'),
            },
            'thumbnails': {},
            'preview_thumbnail': doc.get('preview_thumbnail')
        }
        app.mongo.db.projects.insert_one(new_doc)
        file_path = os.path.join(app.config.get('FS_MEDIA_STORAGE_PATH'), doc.get('folder'), doc.get('filename'))
        task_edit_video.delay(file_path, json_util.dumps(new_doc), request.get_json())
        activity = {
            "action": "EDIT POST",
            "file_id": doc.get('_id'),
            "payload": request.get_json(),
            "create_date": datetime.utcnow()
        }
        app.mongo.db.activity.insert_one(activity)
        return json_response(new_doc)

    def delete(self, project_id):
        """
        Delete project from db and video from filestorage.
        ---
        parameters:
        - name: project_id
          in: path
          type: string
          required: true
          description: Unique project id
        responses:
          200:
            description: OK
            schema:
              type: object
              properties:
                status:
                  type: boolean
                  example: True
                message:
                  type: string
                  example: Delete successfully
        """
        doc = app.mongo.db.projects.find_one({'_id': format_id(project_id)})
        if not doc:
            return not_found("Project with id: {} was not found.".format(project_id))

        # remove record from db
        app.mongo.db.projects.delete_one({'_id': format_id(project_id)})

        # remove file from storage
        file_path = os.path.join(app.config.get('FS_MEDIA_STORAGE_PATH'), doc.get('folder'), doc.get('filename'))

        if app.fs.delete(file_path):
            # Delete thumbnails
            thumbnails = []
            for key in doc['thumbnails'].keys():
                thumbnails = doc['thumbnails'][str(key)]
            for thumbnail in thumbnails:
                path = os.path.join(app.config.get('FS_MEDIA_STORAGE_PATH'), thumbnail['folder'], thumbnail['filename'])
                app.fs.delete(path)
            preview_thumbnail = doc['preview_thumbnail']
            if preview_thumbnail:
                path = os.path.join(app.config.get('FS_MEDIA_STORAGE_PATH'), preview_thumbnail['folder'],
                                    preview_thumbnail['filename'])
                app.fs.delete(path)
            return json_response(status=204)
        else:
            return json_response(status=500)


class ThumbnailsTimelineProject(MethodView):
    SCHEMA_THUMBNAILS = {
        'amount': {
            'type': 'integer',
            'required': True,
            'empty': False,
        },
    }

    def get(self, project_id):
        """
        Edit video. This method creates a new project.
        ---
        consumes:
        - application/json
        parameters:
        - in: path
          name: project_id
          type: string
          required: True
          description: Unique project id
        - name: amount
          in: query
          type: integer
          description: number thumbnails to create
        responses:
          200:
            description: OK
            schema:
              type: object
              properties:
                processing:
                  type: boolean
                  example: True
                thumbnails:
                  type: object
                  example: {}
        """
        # validate user-agent
        check_user_agent()

        amount = int(request.args.get('offset', 40))

        doc = app.mongo.db.projects.find_one({'_id': format_id(project_id)})
        if not doc:
            return not_found("Project with id: {} was not found.".format(project_id))

        # Only get thumbnails when list thumbnail 've not created yet (empty) and video is not processed any task
        data = doc.get('thumbnails')
        if (not data or not data.get(str(amount))) \
                and doc.get('processing') is False:
            # Update processing is True when begin edit video
            app.mongo.db.projects.update_one(
                {'_id': doc['_id']},
                {'$set': {
                    'processing': True,
                    'thumbnails': {}
                }}
            )
            doc['processing'] = True
            doc['thumbnails'] = {}

            # Delete all old thumbnails
            thumbnails = []
            for key in doc['thumbnails'].keys():
                thumbnails = doc['thumbnails'][str(key)]
            for thumbnail in thumbnails:
                path = os.path.join(app.config.get('FS_MEDIA_STORAGE_PATH'), thumbnail['folder'], thumbnail['filename'])
                app.fs.delete(path)
            # Run get list thumbnails of timeline for video in celery
            task_get_list_thumbnails.delay(json_util.dumps(doc), amount)
        return json_response({"processing": doc.get('processing'), "thumbnails": doc['thumbnails']})


class GetRawVideoThumbnail(MethodView):
    def get(self, project_id):
        # get range of video
        video_range = request.headers.environ.get('HTTP_RANGE', 'byte=0-')
        doc = app.mongo.db.projects.find_one_or_404({'_id': format_id(project_id)})
        folder_path = os.path.join(app.config['FS_MEDIA_STORAGE_PATH'], doc['folder'])
        if request.args.get('thumbnail'):
            thumbnail = request.args.get('thumbnail', -1, type=int)
            if not thumbnail >= 0 or thumbnail >= len(doc['thumbnails']['40']):
                return not_found('')
            byte = app.fs.get(folder_path + '/' + doc['thumbnails']['40'][thumbnail]['filename'])
            res = make_response(byte)
            res.headers['Content-Type'] = 'image/png'
            return res
        length = int(doc['metadata'].get('size'))
        start = int(re.split('[= | -]', video_range)[1])
        end = length - 1
        chunksize = end - start + 1
        headers = {
            'Content-Range': f'bytes {start}-{end}/{length}',
            'Accept-Ranges': 'bytes',
            'Content-Length': chunksize,
            'Content-Type': 'video/mp4',
        }
        # get a stack of bytes push to client
        stream = app.fs.get_range(folder_path + '/' + doc['filename'], start, end)
        res = make_response(stream)
        res.headers = headers
        return res, 206


class PreviewThumbnailVideo(MethodView):
    SCHEMA_PREVIEW_THUMBNAIL = {
        'type': {
            'type': 'string',
            'required': True,
            'empty': False,
        },
        'time': {
            'type': 'integer',
            'required': False,
            'empty': False,
        },
        'data': {
            'type': 'binary',
            'required': False,
            'empty': False,
        },
    }

    def post(self, project_id):
        """
        Edit video. This method does not create a new project.
        ---
        consumes:
        - application/json
        parameters:
        - in: path
          name: project_id
          type: string
          required: True
          description: Unique project id
        - in: body
          name: action
          description: Actions want to apply to the video
          required: True
          schema:
            type: object
            properties:
              cut:
                type: object
                properties:
                  start:
                    type: integer
                    example: 5
                  end:
                    type: integer
                    example: 10
              crop:
                type: object
                properties:
                  width:
                    type: integer
                    example: 480
                  height:
                    type: integer
                    example: 360
                  x:
                    type: integer
                    example: 10
                  y:
                    type: integer
                    example: 10
              rotate:
                type: object
                properties:
                  degree:
                    type: integer
                    example: 90
        responses:
          200:
            description: OK
            schema:
              type: object
              properties:
                filename:
                  type: string
                  example: fa5079a38e0a4197864aa2ccb07f3bea.mp4
                folder:
                  type: string
                  example: 2019/5
                metadata:
                  type: object
                  properties:
                    codec_name:
                      type: string
                      example: h264
                    codec_long_name:
                      type: string
                      example: H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10
                    width:
                      type: string
                      example: 640
                    height:
                      type: string
                      example: 360
                    duration:
                      type: string
                      example: 300.014000
                    bit_rate:
                      type: string
                      example: 287654
                    nb_frames:
                      type: string
                      example: 7654
                    r_frame_rate:
                      type: string
                      example: 24/1
                    format_name:
                      type: string
                      example: mov,mp4,m4a,3gp,3g2,mj2
                    size:
                      type: string
                      example: 14567890
                mime_type:
                  type: string
                  example: video/mp4
                create_time:
                  type: object
                  properties:
                    $date:
                      type: integer
                      example: 1556853105063
                original_filename:
                  type: string
                  example: video.mp4
                client_info:
                  type: string
                  example: Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:66.0) Gecko/20100101 Firefox/66.0
                version:
                  type: integer
                  example: 1
                parent:
                  type: object
                  example: {}
                processing:
                  type: boolean
                  example: False
                thumbnails:
                  type: object
                  example: {}
                _id:
                  type: object
                  properties:
                    $oid:
                      type: string
                      example: 5cbd5acfe24f6045607e51aa
        """
        # validate user-agent
        check_user_agent()
        # validate request
        updates = request.get_json()
        check_request_schema_validity(updates, self.SCHEMA_PREVIEW_THUMBNAIL)

        doc = app.mongo.db.projects.find_one({'_id': format_id(project_id)})
        if not doc:
            return not_found("Project with id: {} was not found.".format(project_id))
        if doc.get('processing') is True:
            return forbidden('this video is still processing, please wait.')

        file_path = os.path.join(app.config.get('FS_MEDIA_STORAGE_PATH'), doc.get('folder'), doc.get('filename'))
        preview_thumbnail = self._set_thumbnail(file_path, updates, json_util.dumps(doc))
        if not preview_thumbnail:
            return bad_request('Invalid request')
        else:
            app.mongo.db.projects.update_one(
                {'_id': doc['_id']},
                {'$set': {
                    'preview_thumbnail': preview_thumbnail
                }}
            )
            doc['preview_thumbnail'] = preview_thumbnail
        return json_response(doc)

    def _set_thumbnail(self, video_path, schema, doc):
        action = schema.get('type')
        thumbnail_stream, thumbnail_metadata = None, None
        doc = json_util.loads(doc)
        if action == 'upload':
            thumbnail_stream = schema.get('data')
            thumbnail_metadata = app.fs.get_meta(thumbnail_stream)
        elif action == 'capture':
            time = schema.get('time')
            video_stream = app.fs.get(video_path)
            video_editor = get_video_editor()
            thumbnail_stream, thumbnail_metadata = video_editor.capture_thumbnail(
                video_stream, doc['filename'], doc['metadata'], time
            )

        if thumbnail_stream and thumbnail_metadata:
            try:
                filename, ext = os.path.splitext(doc['filename'])
                thumbnail_filename = f"{filename}_thumbnail.png"
                path = os.path.join(app.config.get('FS_MEDIA_STORAGE_PATH'), doc.get('folder'), thumbnail_filename)
                app.fs.put(thumbnail_stream, path)
                return {
                    'filename': thumbnail_filename,
                    'folder': doc.get('folder'),
                    'mimetype': 'image/png',
                    'width': thumbnail_metadata.get('width'),
                    'height': thumbnail_metadata.get('height'),
                    'size': thumbnail_metadata.get('size'),
                }
            except Exception as exc:
                logger.exception(exc)
        else:
            return {}


# register all urls
bp.add_url_rule('/', view_func=UploadProject.as_view('upload_project'))
bp.add_url_rule('/<path:project_id>', view_func=RetrieveEditDestroyProject.as_view('retrieve_edit_destroy_project'))
bp.add_url_rule('/url_raw/<path:project_id>', view_func=GetRawVideoThumbnail.as_view('get_raw_video_thumbnail'))
bp.add_url_rule('/<path:project_id>/preview_thumbnail',
                view_func=PreviewThumbnailVideo.as_view('preview_thumbnail_video'))
bp.add_url_rule('/<path:project_id>/thumbnails',
                view_func=ThumbnailsTimelineProject.as_view('thumbnails_timeline_project'))
