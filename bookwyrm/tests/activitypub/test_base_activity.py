''' tests the base functionality for activitypub dataclasses '''
from io import BytesIO
import json
import pathlib
from unittest.mock import patch

from dataclasses import dataclass
from django.test import TestCase
from PIL import Image
import responses

from bookwyrm import activitypub
from bookwyrm.activitypub.base_activity import ActivityObject, \
    find_existing_by_remote_id, resolve_remote_id
from bookwyrm.activitypub import ActivitySerializerError
from bookwyrm import models

class BaseActivity(TestCase):
    ''' the super class for model-linked activitypub dataclasses '''
    def setUp(self):
        ''' we're probably going to re-use this so why copy/paste '''
        self.user = models.User.objects.create_user(
            'mouse', 'mouse@mouse.mouse', 'mouseword', local=True)
        self.user.remote_id = 'http://example.com/a/b'
        self.user.save()

        datafile = pathlib.Path(__file__).parent.joinpath(
            '../data/ap_user.json'
        )
        self.userdata = json.loads(datafile.read_bytes())
        # don't try to load the user icon
        del self.userdata['icon']

    def test_init(self):
        ''' simple successfuly init '''
        instance = ActivityObject(id='a', type='b')
        self.assertTrue(hasattr(instance, 'id'))
        self.assertTrue(hasattr(instance, 'type'))

    def test_init_missing(self):
        ''' init with missing required params '''
        with self.assertRaises(ActivitySerializerError):
            ActivityObject()

    def test_init_extra_fields(self):
        ''' init ignoring additional fields '''
        instance = ActivityObject(id='a', type='b', fish='c')
        self.assertTrue(hasattr(instance, 'id'))
        self.assertTrue(hasattr(instance, 'type'))

    def test_init_default_field(self):
        ''' replace an existing required field with a default field '''
        @dataclass(init=False)
        class TestClass(ActivityObject):
            ''' test class with default field '''
            type: str = 'TestObject'

        instance = TestClass(id='a')
        self.assertEqual(instance.id, 'a')
        self.assertEqual(instance.type, 'TestObject')

    def test_serialize(self):
        ''' simple function for converting dataclass to dict '''
        instance = ActivityObject(id='a', type='b')
        serialized = instance.serialize()
        self.assertIsInstance(serialized, dict)
        self.assertEqual(serialized['id'], 'a')
        self.assertEqual(serialized['type'], 'b')

    def test_find_existing_by_remote_id(self):
        ''' attempt to match a remote id to an object in the db '''
        # uses a different remote id scheme
        book = models.Edition.objects.create(
            title='Test Edition', remote_id='http://book.com/book')
        # this isn't really part of this test directly but it's helpful to state
        self.assertEqual(book.origin_id, 'http://book.com/book')
        self.assertNotEqual(book.remote_id, 'http://book.com/book')

        # uses subclasses
        models.Comment.objects.create(
            user=self.user, content='test status', book=book, \
            remote_id='https://comment.net')

        result = find_existing_by_remote_id(models.User, 'hi')
        self.assertIsNone(result)

        result = find_existing_by_remote_id(
            models.User, 'http://example.com/a/b')
        self.assertEqual(result, self.user)

        # test using origin id
        result = find_existing_by_remote_id(
            models.Edition, 'http://book.com/book')
        self.assertEqual(result, book)

        # test subclass match
        result = find_existing_by_remote_id(
            models.Status, 'https://comment.net')

    @responses.activate
    def test_resolve_remote_id(self):
        ''' look up or load remote data '''
        # existing item
        result = resolve_remote_id(models.User, 'http://example.com/a/b')
        self.assertEqual(result, self.user)

        # remote item
        responses.add(
            responses.GET,
            'https://example.com/user/mouse',
            json=self.userdata,
            status=200)

        with patch('bookwyrm.models.user.set_remote_server.delay'):
            result = resolve_remote_id(
                models.User, 'https://example.com/user/mouse')
        self.assertIsInstance(result, models.User)
        self.assertEqual(result.remote_id, 'https://example.com/user/mouse')
        self.assertEqual(result.name, 'MOUSE?? MOUSE!!')

    def test_to_model(self):
        ''' the big boy of this module. it feels janky to test this with actual
        models rather than a test model, but I don't know how to make a test
        model so here we are. '''
        instance = ActivityObject(id='a', type='b')
        with self.assertRaises(ActivitySerializerError):
            instance.to_model(models.User)

        # test setting simple fields
        self.assertEqual(self.user.name, '')
        update_data = activitypub.Person(**self.user.to_activity())
        update_data.name = 'New Name'
        update_data.to_model(models.User, self.user)

        self.assertEqual(self.user.name, 'New Name')

    def test_to_model_foreign_key(self):
        ''' test setting one to one/foreign key '''
        update_data = activitypub.Person(**self.user.to_activity())
        update_data.publicKey['publicKeyPem'] = 'hi im secure'
        update_data.to_model(models.User, self.user)
        self.assertEqual(self.user.key_pair.public_key, 'hi im secure')

    @responses.activate
    def test_to_model_image(self):
        ''' update an image field '''
        update_data = activitypub.Person(**self.user.to_activity())
        update_data.icon = {'url': 'http://www.example.com/image.jpg'}
        image_file = pathlib.Path(__file__).parent.joinpath(
            '../../static/images/default_avi.jpg')
        image = Image.open(image_file)
        output = BytesIO()
        image.save(output, format=image.format)
        image_data = output.getvalue()
        responses.add(
            responses.GET,
            'http://www.example.com/image.jpg',
            body=image_data,
            status=200)

        self.assertIsNone(self.user.avatar.name)
        with self.assertRaises(ValueError):
            self.user.avatar.file #pylint: disable=pointless-statement

        update_data.to_model(models.User, self.user)
        self.assertIsNotNone(self.user.avatar.name)
        self.assertIsNotNone(self.user.avatar.file)
