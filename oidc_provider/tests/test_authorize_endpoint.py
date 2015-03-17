import uuid
try:
    from urllib.parse import unquote, urlencode
except ImportError: # Python 2
    from urllib import unquote, urlencode

from django.contrib.auth import REDIRECT_FIELD_NAME
from django.contrib.auth.models import AnonymousUser
from django.core.urlresolvers import reverse
from django.test import RequestFactory
from django.test import TestCase

from oidc_provider import settings
from oidc_provider.models import *
from oidc_provider.tests.utils import *
from oidc_provider.views import *


class AuthorizationCodeFlowTestCase(TestCase):
    """
    An Authentication Request is an OAuth 2.0 Authorization Request that
    requests that the End-User be authenticated by the Authorization Server.
    """

    def setUp(self):
        self.factory = RequestFactory()
        self.user = create_fake_user()
        self.client = create_fake_client(response_type='code')
        self.state = uuid.uuid4().hex

    def test_missing_parameters(self):
        """
        If the request fails due to a missing, invalid, or mismatching
        redirection URI, or if the client identifier is missing or invalid,
        the authorization server SHOULD inform the resource owner of the error.

        See: https://tools.ietf.org/html/rfc6749#section-4.1.2.1
        """
        url = reverse('oidc_provider:authorize')

        request = self.factory.get(url)

        response = AuthorizeView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(bool(response.content), True)

    def test_invalid_response_type(self):
        """
        The OP informs the RP by using the Error Response parameters defined
        in Section 4.1.2.1 of OAuth 2.0.

        See: http://openid.net/specs/openid-connect-core-1_0.html#AuthError
        """
        # Create an authorize request with an unsupported response_type.
        query_str = urlencode({
            'client_id': self.client.client_id,
            'response_type': 'something_wrong',
            'redirect_uri': self.client.default_redirect_uri,
            'scope': 'openid email',
            'state': self.state,
        }).replace('+', '%20')

        url = reverse('oidc_provider:authorize') + '?' + query_str

        request = self.factory.get(url)

        response = AuthorizeView.as_view()(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.has_header('Location'), True)

        # Should be an 'error' component in query.
        query_exists = 'error=' in response['Location']
        self.assertEqual(query_exists, True)

    def test_codeflow_user_not_logged(self):
        """
        The Authorization Server attempts to Authenticate the End-User by
        redirecting to the login view.

        See: http://openid.net/specs/openid-connect-core-1_0.html#Authenticates
        """
        query_str = urlencode({
            'client_id': self.client.client_id,
            'response_type': 'code',
            'redirect_uri': self.client.default_redirect_uri,
            'scope': 'openid email',
            'state': self.state,
        }).replace('+', '%20')

        url = reverse('oidc_provider:authorize') + '?' + query_str

        request = self.factory.get(url)
        request.user = AnonymousUser()

        response = AuthorizeView.as_view()(request)

        # Check if user was redirected to the login view.
        login_url_exists = settings.get('LOGIN_URL') in response['Location']
        self.assertEqual(login_url_exists, True)

        # Check if the login will redirect to a valid url.
        try:
            next_value = response['Location'].split(REDIRECT_FIELD_NAME + '=')[1]
            next_url = unquote(next_value)
            is_next_ok = next_url == url
        except:
            is_next_ok = False
        self.assertEqual(is_next_ok, True)

    def test_codeflow_user_consent_inputs(self):
        """
        Once the End-User is authenticated, the Authorization Server MUST
        obtain an authorization decision before releasing information to
        the Client.

        See: http://openid.net/specs/openid-connect-core-1_0.html#Consent
        """
        response_type = 'code'
        state = 'openid email'

        query_str = urlencode({
            'client_id': self.client.client_id,
            'response_type': response_type,
            'redirect_uri': self.client.default_redirect_uri,
            'scope': state,
            'state': self.state,
        }).replace('+', '%20')

        url = reverse('oidc_provider:authorize') + '?' + query_str

        request = self.factory.get(url)
        # Simulate that the user is logged.
        request.user = self.user

        response = AuthorizeView.as_view()(request)

        # Check if hidden inputs exists in the form,
        # also if their values are valid.
        input_html = '<input name="{0}" type="hidden" value="{1}" />'

        to_check = {
            'client_id': self.client.client_id,
            'redirect_uri': self.client.default_redirect_uri,
            'response_type': response_type,
        }

        for key, value in to_check.items():
            is_input_ok = input_html.format(key, value) in response.content.decode('ascii')
            self.assertEqual(is_input_ok, True,
                msg='Hidden input for "'+key+'" fails.')

    def test_codeflow_user_consent_response(self):
        """
        First,
        if the user denied the consent we must ensure that
        the error response parameters are added to the query component
        of the Redirection URI.

        Second,
        if the user allow the RP then the server MUST return
        the parameters defined in Section 4.1.2 of OAuth 2.0 [RFC6749]
        by adding them as query parameters to the redirect_uri.
        """
        response_type = 'code'

        url = reverse('oidc_provider:authorize')

        post_data = {
            'client_id': self.client.client_id,
            'redirect_uri': self.client.default_redirect_uri,
            'response_type': response_type,
            'scope': 'openid email',
            'state': self.state,
        }

        request = self.factory.post(url, data=post_data)
        # Simulate that the user is logged.
        request.user = self.user

        response = AuthorizeView.as_view()(request)

        # Because user doesn't allow app, SHOULD exists an error parameter
        # in the query.
        self.assertEqual('error=' in response['Location'], True,
            msg='error param is missing.')
        self.assertEqual('access_denied' in response['Location'], True,
            msg='access_denied param is missing.')

        # Simulate user authorization.
        post_data['allow'] = 'Accept' # Should be the value of the button.

        request = self.factory.post(url, data=post_data)
        # Simulate that the user is logged.
        request.user = self.user

        response = AuthorizeView.as_view()(request)

        # Validate the code returned by the OP.
        code = (response['Location'].split('code='))[1].split('&')[0]
        try:
            code = Code.objects.get(code=code)
            is_code_ok = (code.client == self.client) and \
                         (code.user == self.user)
        except:
            is_code_ok = False
        self.assertEqual(is_code_ok, True,
            msg='Code returned is invalid.')

        # Check if the state is returned.
        state = (response['Location'].split('state='))[1].split('&')[0]
        self.assertEqual(state == self.state, True,
            msg='State change or is missing.')


class AuthorizationImplicitFlowTestCase(TestCase):
    """
    TODO.
    """
    pass
