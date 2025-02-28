# -*- coding: utf-8 -*-
#
# Copyright (C) 2020-2021 CERN.
# Copyright (C) 2020 Northwestern University.
# Copyright (C) 2021 TU Wien.
# Copyright (C) 2021 data-futures.
# Copyright (C) 2022 Universität Hamburg.
#
# Invenio-RDM-Records is free software; you can redistribute it and/or modify
# it under the terms of the MIT License; see LICENSE file for more details.

"""Bibliographic Record Resource."""
from functools import wraps

from flask import abort, current_app, g, redirect, send_file, url_for,request
from invenio_db import db
from flask_cors import cross_origin
from flask_resources import (
    HTTPJSONException,
    Resource,
    ResponseHandler,
    from_conf,
    request_parser,
    resource_requestctx,
    response_handler,
    route,
    with_content_negotiation,
)
from importlib_metadata import version
from invenio_drafts_resources.resources import RecordResource
from invenio_drafts_resources.resources.records.errors import RedirectException
from invenio_records_resources.resources.errors import ErrorHandlersMixin
from invenio_records_resources.resources.records.headers import etag_headers
from invenio_records_resources.resources.records.resource import (
    request_data,
    request_extra_args,
    request_headers,
    request_read_args,
    request_search_args,
    request_view_args,
)
from invenio_records_resources.resources.records.utils import search_preference
from invenio_stats import current_stats
from sqlalchemy.exc import NoResultFound
from werkzeug.utils import secure_filename
from .modal import  SavedRecords
from invenio_accounts.models import LoginInformation
from .serializers import (
    IIIFCanvasV2JSONSerializer,
    IIIFInfoV2JSONSerializer,
    IIIFManifestV2JSONSerializer,
    IIIFSequenceV2JSONSerializer,
)


class RDMRecordResource(RecordResource):
    """RDM record resource."""

    def create_url_rules(self):
        """Create the URL rules for the record resource."""

        def p(route):
            """Prefix a route with the URL prefix."""
            return f"{self.config.url_prefix}{route}"

        routes = self.config.routes
        url_rules = super().create_url_rules()
        url_rules += [
            route("POST", p(routes["item-pids-reserve"]), self.pids_reserve),
            route("DELETE", p(routes["item-pids-reserve"]), self.pids_discard),
            route("GET", p(routes["item-review"]), self.review_read),
            route("PUT", p(routes["item-review"]), self.review_update),
            route("DELETE", p(routes["item-review"]), self.review_delete),
            route("POST", p(routes["item-actions-review"]), self.review_submit),
            route(
                "POST",
                p(routes["record-access-request"]),
                self.create_access_request,
            ),
            route(
                "PUT",
                p(routes["access-request-settings"]),
                self.update_access_settings,
            ),
            route("DELETE", p(routes["delete-record"]), self.delete_record),
            route("POST", p(routes["restore-record"]), self.restore_record),
            route("POST", p(routes["set-record-quota"]), self.set_record_quota),
            # TODO: move to users?
            route("POST", routes["set-user-quota"], self.set_user_quota),
            route("POST", p(routes["saved"]), self.saved),
            route("GET", p(routes["saved"]), self.get_saved)
    ]

        return url_rules
    def userInfo(self):
       for i in LoginInformation.query.all():
            user_id = i.user_id 
       return user_id
        
    useer_id = userInfo
    @response_handler()
    def saved(self):
       user_id = self.userInfo()
       request_data = request.get_json()
       me = SavedRecords(user_id,record_id=request_data["record_id"])
       db.session.add(me)
       db.session.commit()
       return request_data, 200  # HTTP 200 status code.
    
    @request_view_args
    def get_saved(self):
        lists = []
        data ={"hits":[]}
        print(data["hits"])
        user_id = self.userInfo()
        for record in SavedRecords.query.filter_by(user_id=user_id):
             lists.append(record.record_id)
        result = RDMRecordMetadata.query.filter(RDMRecordMetadata.json["id"].in_(lists))
        for r in result:
            data["hits"].append(r.json)
        print("getting saved items")
        print(data["hits"])
        return data
    @request_extra_args
    @request_read_args
    @request_view_args
    @response_handler()
    def read(self):
        """Read an item."""
        try:
            item = self.service.read(
                g.identity,
                resource_requestctx.view_args["pid_value"],
                expand=resource_requestctx.args.get("expand", False),
                # allows to access deleted record if permissions match
                include_deleted=resource_requestctx.args.get("include_deleted", False),
            )
        except NoResultFound:
            # If the parent pid is being used we can get the id of the latest record and redirect
            latest_version = self.service.read_latest(
                g.identity,
                resource_requestctx.view_args["pid_value"],
                expand=resource_requestctx.args.get("expand", False),
            )
            return (
                redirect(
                    url_for(
                        ".read",
                        pid_value=latest_version.id,
                    )
                ),
                None,  # We pass None to create a tuple as the response_handler always expects an iterable
            )

        # we emit the record view stats event here rather than in the service because
        # the service might be called from other places as well that we don't want
        # to count, e.g. from some CLI commands
        emitter = current_stats.get_event_emitter("record-view")
        if item is not None and emitter is not None:
            emitter(current_app, record=item._record, via_api=True)

        return item.to_dict(), 200

    @request_headers
    @request_view_args
    @request_data
    def set_record_quota(self):
        """Set record quota resource."""
        item = self.service.set_quota(
            g.identity,
            resource_requestctx.view_args["pid_value"],
            data=resource_requestctx.data,
        )

        return {}, 200

    @request_headers
    @request_view_args
    @request_data
    def set_user_quota(self):
        """Set user quota resource."""
        item = self.service.set_user_quota(
            g.identity,
            id_=resource_requestctx.view_args["pid_value"],
            data=resource_requestctx.data,
        )

        return {}, 200

    #
    # Deletion workflows
    #
    @request_headers
    @request_view_args
    @request_data
    def delete_record(self):
        """Read the related review request."""
        item = self.service.delete_record(
            g.identity,
            resource_requestctx.view_args["pid_value"],
            resource_requestctx.data,
            revision_id=resource_requestctx.headers.get("if_match"),
        )

        return item.to_dict(), 204

    @request_headers
    @request_view_args
    @request_data
    def restore_record(self):
        """Read the related review request."""
        item = self.service.restore_record(
            g.identity,
            resource_requestctx.view_args["pid_value"],
            resource_requestctx.data,
        )

        return item.to_dict(), 200

    #
    # Review request
    #
    @request_view_args
    @response_handler()  # TODO: probably needs to change
    def review_read(self):
        """Read the related review request."""
        item = self.service.review.read(
            g.identity,
            resource_requestctx.view_args["pid_value"],
        )

        return item.to_dict(), 200

    @request_headers
    @request_view_args
    @request_data  # TODO: probably needs to change
    def review_update(self):
        """Update a review request."""
        item = self.service.review.update(
            g.identity,
            resource_requestctx.view_args["pid_value"],
            resource_requestctx.data,
            revision_id=resource_requestctx.headers.get("if_match"),
        )

        return item.to_dict(), 200

    @request_headers
    @request_view_args
    def review_delete(self):
        """Delete a review request."""
        self.service.review.delete(
            g.identity,
            resource_requestctx.view_args["pid_value"],
            revision_id=resource_requestctx.headers.get("if_match"),
        )
        return "", 204

    @request_headers
    @request_view_args
    @request_data
    def review_submit(self):
        """Submit a draft for review or directly publish it."""
        require_review = False
        if resource_requestctx.data:
            require_review = resource_requestctx.data.pop("require_review", False)

        item = self.service.review.submit(
            g.identity,
            resource_requestctx.view_args["pid_value"],
            resource_requestctx.data,
            require_review=require_review,
        )
        return item.to_dict(), 202

    #
    # PIDs
    #
    @request_extra_args
    @request_view_args
    @response_handler()
    def pids_reserve(self):
        """Reserve a PID."""
        item = self.service.pids.create(
            identity=g.identity,
            id_=resource_requestctx.view_args["pid_value"],
            scheme=resource_requestctx.view_args["scheme"],
            expand=resource_requestctx.args.get("expand", False),
        )

        return item.to_dict(), 201

    @request_extra_args
    @request_view_args
    @response_handler()
    def pids_discard(self):
        """Discard a previously reserved PID."""
        item = self.service.pids.discard(
            identity=g.identity,
            id_=resource_requestctx.view_args["pid_value"],
            scheme=resource_requestctx.view_args["scheme"],
            expand=resource_requestctx.args.get("expand", False),
        )

        return item.to_dict(), 200

    @request_view_args
    @request_data
    def create_access_request(self):
        """Request access to a record as authenticated user."""
        item = self.service.access.request_access(
            id_=resource_requestctx.view_args["pid_value"],
            identity=g.identity,
            data=resource_requestctx.data,
        )
        # TODO: improve the serialization here
        # this is done due to guest access request creation returning a dictionary,
        # not the request item (request item does not exist before email is confirmed)
        if isinstance(item, dict):
            return item, 200
        return item.to_dict(), 200

    @request_view_args
    @request_data
    def update_access_settings(self):
        """Update record access settings."""
        item = self.service.access.update_access_settings(
            id_=resource_requestctx.view_args["pid_value"],
            identity=g.identity,
            data=resource_requestctx.data,
        )
        return item.to_dict(), 200


class RDMRecordCommunitiesResource(ErrorHandlersMixin, Resource):
    """Record communities resource."""

    def __init__(self, config, service):
        """Constructor."""
        super().__init__(config)
        self.service = service

    def create_url_rules(self):
        """Create the URL rules for the record resource."""
        routes = self.config.routes
        url_rules = [
            route("GET", routes["list"], self.search),
            route("POST", routes["list"], self.add),
            route("DELETE", routes["list"], self.remove),
            route("GET", routes["suggestions"], self.get_suggestions),
            route("PUT", routes["list"], self.set_default),
        ]
        return url_rules

    @request_search_args
    @request_view_args
    @response_handler(many=True)
    def search(self):
        """Search for record's communities."""
        items = self.service.search(
            identity=g.identity,
            id_=resource_requestctx.view_args["pid_value"],
            params=resource_requestctx.args,
            search_preference=search_preference(),
            expand=resource_requestctx.args.get("expand", False),
        )
        return items.to_dict(), 200

    @request_view_args
    @response_handler()
    @request_data
    def add(self):
        """Include record in communities."""
        processed, errors = self.service.add(
            identity=g.identity,
            id_=resource_requestctx.view_args["pid_value"],
            data=resource_requestctx.data,
        )

        response = {}
        if processed:
            response["processed"] = processed
        if errors:
            response["errors"] = errors

        # TODO why not checking errors
        return response, 200 if len(processed) > 0 else 400

    @request_view_args
    @request_data
    @response_handler()
    def remove(self):
        """Remove communities from the record."""
        processed, errors = self.service.remove(
            identity=g.identity,
            id_=resource_requestctx.view_args["pid_value"],
            data=resource_requestctx.data,
        )

        response = {}
        if errors:
            response["errors"] = errors

        return response, 200 if len(processed) > 0 else 400

    @request_extra_args
    @request_search_args
    @request_view_args
    @response_handler(many=True)
    def get_suggestions(self):
        """Search for record's communities."""
        by_membership = resource_requestctx.args.get("membership", False)

        items = self.service.search_suggested_communities(
            identity=g.identity,
            id_=resource_requestctx.view_args["pid_value"],
            params=resource_requestctx.args,
            search_preference=search_preference(),
            by_membership=by_membership,
            expand=resource_requestctx.args.get("expand", False),
        )
        return items.to_dict(), 200

    @request_view_args
    @request_data
    def set_default(self):
        """Set default community."""
        item = self.service.set_default(
            id_=resource_requestctx.view_args["pid_value"],
            identity=g.identity,
            data=resource_requestctx.data,
        )

        return item, 200


class RDMRecordRequestsResource(ErrorHandlersMixin, Resource):
    """Record requests resource."""

    def __init__(self, config, service):
        """Constructor."""
        super().__init__(config)
        self.service = service

    def create_url_rules(self):
        """Create the URL rules for the record resource."""
        routes = self.config.routes
        url_rules = [
            route("GET", routes["list"], self.search),
        ]
        return url_rules

    @request_extra_args
    @request_search_args
    @request_view_args
    @response_handler(many=True)
    def search(self):
        """Search for record's requests.

        GET /records/<pid>/requests
        """
        items = self.service.search(
            identity=g.identity,
            params=resource_requestctx.args,
            record_pid=resource_requestctx.view_args["record_pid"],
            search_preference=search_preference(),
            expand=resource_requestctx.args.get("expand", False),
        )

        return items.to_dict(), 200


#
# Parent Record Links
#
class RDMParentRecordLinksResource(RecordResource):
    """Secret links resource."""

    def create_url_rules(self):
        """Create the URL rules for the record resource."""

        def p(route):
            """Prefix a route with the URL prefix."""
            return f"{self.config.url_prefix}{route}"

        routes = self.config.routes
        return [
            route("GET", p(routes["list"]), self.search),
            route("POST", p(routes["list"]), self.create),
            route("GET", p(routes["item"]), self.read),
            route("PUT", p(routes["item"]), self.update),
            route("PATCH", p(routes["item"]), self.partial_update),
            route("DELETE", p(routes["item"]), self.delete),
        ]

    @request_view_args
    @request_data
    @response_handler()
    def create(self):
        """Create a secret link for a record."""
        item = self.service.access.create_secret_link(
            id_=resource_requestctx.view_args["pid_value"],
            identity=g.identity,
            data=resource_requestctx.data,
        )

        return item.to_dict(), 201

    @request_view_args
    @response_handler()
    def read(self):
        """Read a secret link for a record."""
        item = self.service.access.read_secret_link(
            identity=g.identity,
            id_=resource_requestctx.view_args["pid_value"],
            link_id=resource_requestctx.view_args["link_id"],
        )
        return item.to_dict(), 200

    def update(self):
        """Update a secret link for a record."""
        abort(405)

    @request_view_args
    @request_data
    @response_handler()
    def partial_update(self):
        """Patch a secret link for a record."""
        item = self.service.access.update_secret_link(
            id_=resource_requestctx.view_args["pid_value"],
            identity=g.identity,
            link_id=resource_requestctx.view_args["link_id"],
            data=resource_requestctx.data,
        )
        return item.to_dict(), 200

    @request_view_args
    def delete(self):
        """Delete a a secret link for a record."""
        self.service.access.delete_secret_link(
            id_=resource_requestctx.view_args["pid_value"],
            identity=g.identity,
            link_id=resource_requestctx.view_args["link_id"],
        )
        return "", 204

    @request_search_args
    @request_view_args
    @response_handler(many=True)
    def search(self):
        """List secret links for a record."""
        items = self.service.access.read_all_secret_links(
            id_=resource_requestctx.view_args["pid_value"],
            identity=g.identity,
        )
        return items.to_dict(), 200


class RDMParentGrantsResource(RecordResource):
    """Access grants resource."""

    def create_url_rules(self):
        """Create the URL rules for the record resource."""

        def p(route_name):
            """Prefix a route with the URL prefix."""
            return f"{self.config.url_prefix}{self.config.routes[route_name]}"

        return [
            route("GET", p("list"), self.search),
            route("POST", p("list"), self.create),
            route("GET", p("item"), self.read),
            route("PUT", p("item"), self.update),
            route("PATCH", p("item"), self.partial_update),
            route("DELETE", p("item"), self.delete),
        ]

    @request_extra_args
    @request_view_args
    @response_handler()
    def read(self):
        """Read an access grant for a record."""
        item = self.service.access.read_grant(
            identity=g.identity,
            id_=resource_requestctx.view_args["pid_value"],
            grant_id=resource_requestctx.view_args["grant_id"],
            expand=resource_requestctx.args.get("expand", False),
        )
        return item.to_dict(), 200

    @request_extra_args
    @request_view_args
    @request_data
    @response_handler()
    def create(self):
        """Create an access grant for a record."""
        data = resource_requestctx.data
        data["origin"] = f"api:{g.identity.id}"
        item = self.service.access.create_grant(
            identity=g.identity,
            id_=resource_requestctx.view_args["pid_value"],
            data=data,
            expand=resource_requestctx.args.get("expand", False),
        )
        return item.to_dict(), 201

    @request_extra_args
    @request_view_args
    @request_data
    @response_handler()
    def update(self):
        """Update an access grant for a record."""
        item = self.service.access.update_grant(
            identity=g.identity,
            id_=resource_requestctx.view_args["pid_value"],
            grant_id=resource_requestctx.view_args["grant_id"],
            data=resource_requestctx.data,
            expand=resource_requestctx.args.get("expand", False),
            partial=False,
        )
        return item.to_dict(), 200

    @request_extra_args
    @request_view_args
    @request_data
    @response_handler()
    def partial_update(self):
        """Patch an access grant for a record."""
        item = self.service.access.update_grant(
            identity=g.identity,
            id_=resource_requestctx.view_args["pid_value"],
            grant_id=resource_requestctx.view_args["grant_id"],
            data=resource_requestctx.data,
            expand=resource_requestctx.args.get("expand", False),
            partial=True,
        )
        return item.to_dict(), 200

    @request_view_args
    def delete(self):
        """Delete an access grant for a record."""
        self.service.access.delete_grant(
            identity=g.identity,
            id_=resource_requestctx.view_args["pid_value"],
            grant_id=resource_requestctx.view_args["grant_id"],
        )
        return "", 204

    @request_extra_args
    @request_search_args
    @request_view_args
    @response_handler(many=True)
    def search(self):
        """List access grants for a record."""
        items = self.service.access.read_all_grants(
            identity=g.identity,
            id_=resource_requestctx.view_args["pid_value"],
            expand=resource_requestctx.args.get("expand", False),
        )
        return items.to_dict(), 200


#
# Community's records
#
class RDMCommunityRecordsResource(RecordResource):
    """RDM community's records resource."""

    def create_url_rules(self):
        """Create the URL rules for the record resource."""

        def p(route):
            """Prefix a route with the URL prefix."""
            return f"{self.config.url_prefix}{route}"

        routes = self.config.routes
        url_rules = [
            route("GET", p(routes["list"]), self.search),
            route("DELETE", p(routes["list"]), self.delete),
        ]

        return url_rules

    @request_search_args
    @request_view_args
    @response_handler(many=True)
    def search(self):
        """Perform a search over the community's records."""
        hits = self.service.search(
            identity=g.identity,
            community_id=resource_requestctx.view_args["pid_value"],
            params=resource_requestctx.args,
            search_preference=search_preference(),
        )
        return hits.to_dict(), 200

    @request_view_args
    @response_handler()
    @request_data
    def delete(self):
        """Removes records from the communities.

        DELETE /communities/<pid_value>/records
        """
        errors = self.service.delete(
            identity=g.identity,
            community_id=resource_requestctx.view_args["pid_value"],
            data=resource_requestctx.data,
        )
        response = {}
        if errors:
            response["errors"] = errors
        return response, 200


# IIIF decorators

iiif_request_view_args = request_parser(
    from_conf("request_view_args"), location="view_args"
)


def with_iiif_content_negotiation(serializer):
    """Always response as JSON LD regardless of the request type."""
    return with_content_negotiation(
        response_handlers={
            "application/ld+json": ResponseHandler(serializer(), headers=etag_headers),
        },
        default_accept_mimetype="application/ld+json",
    )


class IIIFResource(ErrorHandlersMixin, Resource):
    """IIIF resource."""

    def __init__(self, config, service):
        """Constructor."""
        super().__init__(config)
        self.service = service

    def create_url_rules(self):
        """Create the URL rules for the IIIF resource."""
        routes = self.config.routes
        return [
            route("GET", routes["manifest"], self.manifest),
            route("GET", routes["sequence"], self.sequence),
            route("GET", routes["canvas"], self.canvas),
            route("GET", routes["image_base"], self.base),
            route("GET", routes["image_info"], self.info),
            route("GET", routes["image_api"], self.image_api),
        ]

    def _get_record_with_files(self):
        uuid = resource_requestctx.view_args["uuid"]
        return self.service.read_record(uuid=uuid, identity=g.identity)

    #
    # IIIF Manifest - not all clients support content-negotiation so we need a
    # full endpoint.
    #
    # See https://iiif.io/api/presentation/2.1/#responses on
    # "Access-Control-Allow-Origin: *"
    #
    @cross_origin(origin="*", methods=["GET"])
    @with_iiif_content_negotiation(IIIFManifestV2JSONSerializer)
    @iiif_request_view_args
    @response_handler()
    def manifest(self):
        """Manifest."""
        return self._get_record_with_files().to_dict(), 200

    @cross_origin(origin="*", methods=["GET"])
    @with_iiif_content_negotiation(IIIFSequenceV2JSONSerializer)
    @iiif_request_view_args
    @response_handler()
    def sequence(self):
        """Sequence."""
        return self._get_record_with_files().to_dict(), 200

    @cross_origin(origin="*", methods=["GET"])
    @with_iiif_content_negotiation(IIIFCanvasV2JSONSerializer)
    @iiif_request_view_args
    @response_handler()
    def canvas(self):
        """Canvas."""
        uuid = resource_requestctx.view_args["uuid"]
        key = resource_requestctx.view_args["file_name"]
        file_ = self.service.get_file(uuid=uuid, identity=g.identity, key=key)
        return file_.to_dict(), 200

    @cross_origin(origin="*", methods=["GET"])
    @with_iiif_content_negotiation(IIIFInfoV2JSONSerializer)
    @iiif_request_view_args
    @response_handler()
    def base(self):
        """Base."""
        item = self.service.get_file(
            identity=g.identity,
            uuid=resource_requestctx.view_args["uuid"],
        )
        raise RedirectException(item["links"]["iiif_info"])

    @cross_origin(origin="*", methods=["GET"])
    @with_iiif_content_negotiation(IIIFInfoV2JSONSerializer)
    @iiif_request_view_args
    @response_handler()
    def info(self):
        """Get IIIF image info."""
        item = self.service.get_file(
            identity=g.identity,
            uuid=resource_requestctx.view_args["uuid"],
        )
        return item.to_dict(), 200

    @cross_origin(origin="*", methods=["GET"])
    @request_headers
    @request_read_args
    @iiif_request_view_args
    def image_api(self):
        """IIIF API Implementation.

        .. note::
            * IIF IMAGE API v1.0
                * For more infos please visit <http://iiif.io/api/image/>.
            * IIIF Image API v2.0
                * For more infos please visit <http://iiif.io/api/image/2.0/>.
            * The API works only for GET requests
            * The image process must follow strictly the following workflow:
                * Region
                * Size
                * Rotation
                * Quality
                * Format
        """
        image_format = resource_requestctx.view_args["image_format"]
        uuid = resource_requestctx.view_args["uuid"]
        region = resource_requestctx.view_args["region"]
        size = resource_requestctx.view_args["size"]
        rotation = resource_requestctx.view_args["rotation"]
        quality = resource_requestctx.view_args["quality"]
        to_serve = self.service.image_api(
            identity=g.identity,
            uuid=uuid,
            region=region,
            size=size,
            rotation=rotation,
            quality=quality,
            image_format=image_format,
        )
        # decide the mime_type from the requested image_format
        mimetype = self.config.supported_formats.get(image_format, "image/jpeg")
        # TODO: get from cache on the service image.last_modified
        last_modified = None
        send_file_kwargs = {"mimetype": mimetype}
        # last_modified is not supported before flask 0.12
        if last_modified:
            send_file_kwargs.update(last_modified=last_modified)

        dl = resource_requestctx.args.get("dl")
        if dl is not None:
            filename = secure_filename(dl)
            if filename.lower() in {"", "1", "true"}:
                filename = "{0}-{1}-{2}-{3}-{4}.{5}".format(
                    uuid, region, size, quality, rotation, image_format
                )

            send_file_kwargs.update(
                as_attachment=True,
            )
            if version("Flask") < "2.2.0":
                send_file_kwargs.update(
                    attachment_filename=secure_filename(filename),
                )
            else:
                # Flask 2.2 renamed `attachment_filename` to `download_name`
                send_file_kwargs.update(
                    download_name=secure_filename(filename),
                )
        if_modified_since = resource_requestctx.headers.get("If-Modified-Since")
        if if_modified_since and last_modified and if_modified_since >= last_modified:
            raise HTTPJSONException(code=304)

        response = send_file(to_serve, **send_file_kwargs)
        return response
