import glob
import logging
import os

from .interface import MediaStorageInterface

logger = logging.getLogger(__name__)


class FileSystemStorage(MediaStorageInterface):

    def get(self, file_path):
        """
        Get stream file
        :param file_path: full file path
        :return:
        """
        try:
            media_file = (open(file_path, 'rb')).read()
        except Exception as ex:
            logger.error('Cannot get data file %s ex: %s' % (file_path, ex))
            media_file = None
        return media_file

    def put(self, content, file_path):
        """
        Put a file into storage
        :param content: stream of file, binary type
        :param file_path: full file path
        :return: True or False
        """
        try:
            # check if dir exists, if not create it
            file_dir = os.path.dirname(file_path)
            if not os.path.exists(file_dir):
                os.makedirs(file_dir)
            # write stream to file
            with open(file_path, "wb") as f:
                f.write(content)
            logger.info('Put media file %s to storage' % file_path)
            return True
        except Exception as ex:
            logger.error('Cannot put file %s ex: %s' % (file_path, ex))
            return False

    def replace(self, file_path):
        pass

    def delete(self, file_path):
        try:
            filename, ext = os.path.splitext(file_path)
            file_lists = [file_path] + glob.glob(f'{filename}_timeline_*.png')
            for path in file_lists:
                if os.path.exists(path):
                    os.remove(path)
            logger.info('Deleted media file %s from storage' % file_path)
            return True
        except Exception as ex:
            logger.error('Cannot delete file %s ex: %s' % (file_path, ex))
            return False
