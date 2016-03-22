# -*- coding: utf-8 -*-

from flask import Blueprint, current_app, request, render_template, abort, url_for, redirect
from pymongo import DESCENDING
from bson import ObjectId

from widukind_web import constants
from widukind_web.extensions import auth
from widukind_web.extensions import cache
from widukind_web import queries

bp = Blueprint('admin', __name__)

#TODO: redis cache view


@bp.route('/providers', endpoint="providers")
@auth.required
def all_providers():
    cursor = queries.col_providers().find({})
    providers = [doc for doc in cursor]
    
    datasets_counters = queries.datasets_counter_status()
    
    return render_template("admin/providers.html", providers=providers, 
                           datasets_counters=datasets_counters)

@bp.route('/datasets/<slug>', endpoint="datasets")
@auth.required
def all_datasets_for_provider_slug(slug):        

    provider = queries.col_providers().find_one({"slug": slug})
    if not provider:
        abort(404)

    projection = {"dimension_list": False, "attribute_list": False,
                  "concepts": False, "codelists": False}
    datasets = queries.col_datasets().find({"provider_name": provider["name"]},
                                           projection)
    
    series_counters = queries.series_counter(match={"provider_name": provider["name"]})

    return render_template("admin/datasets.html", 
                           provider=provider,
                           series_counters=series_counters, 
                           datasets=datasets)

@bp.route('/enable/provider/<slug>', endpoint="provider_enable")
@auth.required
def change_status_provider(slug):

    query = {"slug": slug}
    provider = queries.col_providers().find_one(query)
    if not provider:
        abort(404)
        
    query_update = {}
    if provider["enable"]:
        query_update["enable"] = False
    else:
        query_update["enable"] = True
        
    queries.col_providers().find_one_and_update(query, {"$set": query_update})
    
    datasets_query = {"provider_name": provider["name"]}
    queries.col_datasets().update_many(datasets_query, {"$set": query_update})
    
    return redirect(url_for(".providers"))

@bp.route('/enable/dataset/<slug>', endpoint="dataset_enable")
@auth.required
def change_status_dataset(slug):

    query = {"slug": slug}
    dataset = queries.col_datasets().find_one(query)
    if not dataset:
        abort(404)
        
    query_update = {}
    if dataset["enable"]:
        query_update["enable"] = False
    else:
        query_update["enable"] = True
        
    queries.col_datasets().find_one_and_update(query, {"$set": query_update})

    query = {"name": dataset["provider_name"]}
    provider = queries.col_providers().find_one(query)
    
    return redirect(url_for(".datasets", slug=provider["slug"]))

@bp.route('/db/profiling/<int:status>', endpoint="db-profiling")
@auth.required
def change_db_profiling(status=0):
    db = current_app.widukind_db
    db.set_profiling_level(status)
    return redirect(url_for("home"))

@bp.route('/cache/clear', endpoint="cache_clear")
@auth.required
def cache_clear():
    cache.clear()
    return redirect(url_for("home"))

@bp.route('/doc/<col>/<objectid:objectid>', endpoint="doc")
@auth.required
def doc_view(col, objectid):
    doc = current_app.widukind_db[col].find_one({"_id": objectid})
    return render_template("admin/doc.html", doc=doc) 

@bp.route('/db/profile/<int:position>', endpoint="profile-unit")
@bp.route('/db/profile', endpoint="profile")
@auth.required
def profile_view(position=-1):
    exclude_ns = []
    exclude_ns.append("%s.%s" % (current_app.widukind_db.name, constants.COL_SESSION))
    exclude_ns.append("%s.system.profile" % current_app.widukind_db.name)
    query = {"ns": {"$nin": exclude_ns}}
    docs = current_app.widukind_db["system.profile"].find(query).sort([("ts", -1)]).limit(20)
    
    if position >= 0:
        doc = docs[position]
        return render_template("admin/doc.html", doc=doc) 
    
    return render_template("admin/profile.html", docs=docs) 

@bp.route('/db/stats/<col>', endpoint="col-stats")
@auth.required
def collection_stats_view(col):
    doc = current_app.widukind_db.command("collstats", col)
    return render_template("admin/doc.html", doc=doc) 

@bp.route('/queries', endpoint="queries")
@auth.required
def queries_view():
    
    is_ajax = request.args.get('json') or request.is_xhr
    
    if not is_ajax:
        return render_template("admin/queries.html")
    
    col = current_app.widukind_db[constants.COL_QUERIES]
    
    tags = request.args.get('tags')
    
    q = {}

    if tags:
        tags = tags.split(",")
        q['tags'] = {"$in": tags}
    
    object_list = col.find(q).sort("created", DESCENDING)

    result = []
    for obj in object_list:
        obj["view"] = url_for(".doc", col=constants.COL_QUERIES, objectid=obj["_id"])
        result.append(obj)

    return current_app.jsonify(result)

@bp.route('/logs', endpoint="logs")
@auth.required
def view_logs():
    """
    {
        "_id" : ObjectId("5665a7182d4b25012092ac71"),
        "message" : "change count for BEA.datasets. old[0] - new[0]",
        "level" : "INFO",
        "timestamp" : Timestamp(1449502488, 860),
        "loggerName" : "widukind_web",
        "thread" : 74212736,
        "threadName" : "DummyThread-1",
        "method" : "upsert",
        "lineNumber" : 271,
        "module" : "wsgi",
        "fileName" : "V:\\git\\cepremap\\src\\widukind-web\\widukind_web\\wsgi.py"
    }    
    """
    is_ajax = request.args.get('json') or request.is_xhr
    
    if not is_ajax:
        return render_template("admin/logs.html")
    
    col = current_app.widukind_db[constants.COL_LOGS]
    
    object_list = col.find({})

    return current_app.jsonify(list(object_list))

@bp.route('/stats/series', endpoint="stats-series")
@auth.required
def stats_series():

    cursor = queries.col_providers().find({}, {"name": True})
    provider_names = [doc["name"] for doc in cursor]
    
    result = []
    for provider in provider_names:
        result.append({"_id": provider, "count": queries.col_series().count({"provider_name": provider})})
    
    #result = list(queries.col_series().aggregate([{"$group": {"_id": "$provider_name", "count": {"$sum": 1}}}, {"$sort": {"count": -1} }], allowDiskUse=True))        

    return render_template("admin/stats-series.html", 
                           result=result)
    
@bp.route('/stats/datasets', endpoint="stats-datasets")
@auth.required
def stats_datasets():
    
    result = list(queries.col_datasets().aggregate([{"$group": {"_id": "$provider_name", "count": {"$sum": 1}}}, {"$sort": {"count": -1} }], allowDiskUse=True))        

    return render_template("admin/stats-datasets.html", 
                           result=result)