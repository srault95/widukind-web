# -*- coding: utf-8 -*-

from pprint import pprint
from collections import OrderedDict

from flask import (Blueprint, 
                   current_app, 
                   request, 
                   render_template,
                   url_for, 
                   session, 
                   flash, 
                   abort)

from werkzeug.wsgi import wrap_file

import arrow
from slugify import slugify

from pymongo import ASCENDING, DESCENDING

from widukind_web import constants
from widukind_web.extensions import cache
from widukind_web import queries
from widukind_common.flask_utils import json_tools

bp = Blueprint('views', __name__)

def complex_queries_series(query={}):

    """
    startDate = arrow.get(request.args.get('startDate')).floor('day').datetime
    endDate = arrow.get(request.args.get('endDate')).ceil('day').datetime
    """
    
    search_fields = []
    query_and = []
    
    for r in request.args.lists():
        if r[0] in ['limit', 'tags', 'provider', 'dataset', 'search', 'period']:
            continue
        elif r[0] == 'frequency':
            query['frequency'] = r[1][0]
        elif r[0].startswith('dimensions_'):
            field_name = r[0].split('dimensions_')[1]
            search_fields.append((field_name, r[1][0]))
        else:
            msg = "unknow field[%s]" % r[0]
            current_app.logger.warn(msg)

    tags = request.args.get('tags')

    if search_fields:
        
        query_or_by_field = {}
        query_nor_by_field = {}

        for field, value in search_fields:
            values = value.split()
            value = [v.lower().strip() for v in values]
            
            dim_field = field.lower()
            
            for v in value:
                if v.startswith("!"):
                    if not dim_field in query_nor_by_field:
                        query_nor_by_field[dim_field] = []
                    query_nor_by_field[dim_field].append(v[1:])
                else:
                    if not dim_field in query_or_by_field:
                        query_or_by_field[dim_field] = []
                    query_or_by_field[dim_field].append(v)
        
        for key, values in query_or_by_field.items():
            q_or = {"$or": [
                {"dimensions.%s" % key: {"$in": values}},
            ]}
            query_and.append(q_or)

        for key, values in query_nor_by_field.items():
            q_or = {"$nor": [
                {"dimensions.%s" % key: {"$in": values}},
            ]}
            query_and.append(q_or)

    if query_and:
        query["$and"] = query_and

    if tags and len(tags.split()) > 0:
        tags = list(set(tags.split()))
        query["tags"] = {"$all": tags}
        #conditions = [{"tags": {"$regex": ".*%s.*" % value.lower()}} for value in tags]
        #query_and.append({"": conditions})
        #query_and.append({"$and": conditions})


    print("-----complex query-----")
    pprint(query)    
    print("-----------------------")
        
    return query
    
def datas_from_series(series):
    
    import pandas
    
    for value in series["values"]:
        yield value["period"], pandas.Period(value["period"], freq=series['frequency']).to_timestamp().strftime("%Y-%m-%d"), value["value"]        

@bp.route('/ajax/providers', endpoint="ajax-providers-list")
def ajax_providers_list():
    query = {"enable": True}
    projection = {"_id": False, "slug": True, "name": True}
    docs = [doc for doc in queries.col_providers().find(query, projection).sort("name", ASCENDING)]
    return json_tools.json_response(docs)

@bp.route('/ajax/providers/<provider>/datasets', endpoint="ajax-datasets-list")
def ajax_datasets_list(provider):
    provider_doc = queries.get_provider(provider)
    query = {'provider_name': provider_doc["name"],
             "enable": True}
    projection = {"_id": False, 
                  "dataset_code": True, "name": True, "slug": True}
    
    is_meta = request.args.get('is_meta', type=int, default=0) == 1
    if is_meta:
        projection["metadata"] = True
        
    docs = [doc for doc in queries.col_datasets().find(query, projection)]
    return json_tools.json_response(docs)

@bp.route('/ajax/datasets/<dataset>/dimensions/keys', endpoint="ajax-datasets-dimensions-keys")
def ajax_datasets_dimensions_keys(dataset):
    projection = {"_id": False, 'enable': True, "dimension_keys": True, "concepts": True}
    doc = queries.get_dataset(dataset, projection)
    
    dimensions = [] 
    for key in doc["dimension_keys"]:
        if key in doc["concepts"]:            
            dimensions.append({"value": key, "text": doc["concepts"][key] })
        else:
            dimensions.append({"value": key, "text": key })
    return json_tools.json_response(dimensions)

@bp.route('/ajax/datasets/<dataset>/dimensions/all', endpoint="ajax-datasets-dimensions-all")
def ajax_datasets_dimensions_all(dataset):
    projection = {"_id": False, "enable": True, "dimension_keys": True, "concepts": True, "codelists": True}
    doc = queries.get_dataset(dataset, projection)

    dimensions = []
    print("dimension_keys : ", doc["dimension_keys"])
    for key in doc["dimension_keys"]:
        if key in doc["codelists"] and doc["codelists"][key]:
            dimensions.append({
                "key": key,
                "name": doc["concepts"].get(key) or key,
                "codes": doc["codelists"][key],
            })
    return json_tools.json_response(dimensions)

@bp.route('/ajax/dataset/<dataset>/frequencies', endpoint="ajax-datasets-frequencies")
def ajax_dataset_frequencies(dataset):
    projection = {"_id": False, "lock": False, "tags": False}
    doc = queries.get_dataset(dataset, projection)
    query = {"provider_name": doc["provider_name"],
             "dataset_code": doc["dataset_code"]}
    
    if "metadata" in doc and doc["metadata"].get("frequencies"):
        frequencies = doc["metadata"].get("frequencies")
    else:
        frequencies = queries.col_series().distinct("frequency", filter=query)

    freqs = []
    for freq in frequencies:
        if freq in constants.FREQUENCIES_DICT:
            freqs.append({"value": freq, "text": constants.FREQUENCIES_DICT[freq]})
        else:
            freqs.append({"value": freq, "text": freq})
    return json_tools.json_response(freqs)

@bp.route('/ajax/slug/dataset/<slug>', endpoint="ajax-dataset-by-slug")
def ajax_dataset_with_slug(slug):

    dataset = queries.get_dataset(slug, {"_id": False})

    provider = queries.col_providers().find_one({"name": dataset['provider_name']})

    count_series = queries.col_series().count({"provider_name": dataset['provider_name'],
                                               "dataset_code": dataset['dataset_code']})
    
    return render_template("dataset-unit-modal.html",
                             dataset=dataset, 
                             provider=provider, 
                             count=count_series)

@bp.route('/ajax/slug/series/<slug>', endpoint="ajax-series-by-slug")
def ajax_series_with_slug(slug):
    
    is_reverse = request.args.get('reverse')

    query = {"slug": slug}    
    series = queries.col_series().find_one(query)

    if not series:
        abort(404)
        
    provider = queries.col_providers().find_one({"enable": True,
                                                 "name": series['provider_name']})
    if not provider:
        abort(404)

    dataset = queries.col_datasets().find_one({"enable": True,
                                               'provider_name': series['provider_name'],
                                               "dataset_code": series['dataset_code']})
    if not dataset:
        abort(404)

    max_revisions = 0
    revision_dates = []
    obs_attributes_keys = []
    obs_attributes_values = []
    
    for v in series["values"]:
        
        if "revisions" in v:
            for r in v["revisions"]:
                if not r["revision_date"] in revision_dates:
                    revision_dates.append(r["revision_date"])

            count = len(v["revisions"])
            if count > max_revisions:
                max_revisions = count
        
        if v.get("attributes"):
            for key, attr in v["attributes"].items():
                obs_attributes_keys.append(key)
                obs_attributes_values.append(attr)       
    
    revision_dates.reverse()
    #pprint(revision_dates)
    
    dimension_filter = ".".join([series["dimensions"][key] for key in dataset["dimension_keys"]])
    
    result = render_template(
                    "series-unit-modal.html",
                    series=series,
                    provider=provider,
                    dataset=dataset,
                    dimension_filter=dimension_filter.upper(),
                    is_reverse=is_reverse,
                    obs_attributes_keys=list(set(obs_attributes_keys)),
                    obs_attributes_values=list(set(obs_attributes_values)),
                    revision_dates=list(set(revision_dates)),
                    max_revisions=max_revisions)
    
    return result
    
    
def send_file_csv(fileobj, mimetype=None, content_length=0):

    data = wrap_file(request.environ, fileobj, buffer_size=1024*256)
    response = current_app.response_class(
                        data,
                        mimetype=mimetype,
                        #direct_passthrough=True
                        )
    response.status_code = 200    
    #response.content_length = content_length
    response.make_conditional(request)
    return response

@bp.route('/ajax/plot/series/<slug>', endpoint="ajax_series_plot")
def ajax_plot_series(slug):
    
    series = queries.col_series().find_one({"slug": slug})

    if not series:
        abort(404)

    ds_projection = {"enable": True}
    dataset = queries.col_datasets().find_one({"provider_name": series["provider_name"],
                                               "dataset_code": series["dataset_code"]},
                                              ds_projection)    
    if not dataset:
        abort(404)
    if dataset["enable"] is False:
        abort(307)

    meta = {
        "provider_name": series["provider_name"],
        "dataset_code": series["dataset_code"],
        "name": series["name"],
        "key": series["key"],
        "slug": series["slug"],
    }
    datas = []    
    #datas.append(("Dates", "Values"))
    for period, period_ts, value in datas_from_series(series):
        datas.append({"period": period, "period_ts": str(period_ts), "value": value})
    
    return json_tools.json_response(datas, meta)

@bp.route('/tree', endpoint="tree_root_base")
@bp.route('/tree/<provider>', endpoint="tree_root")
@cache.cached(360)
def tree_view(provider=None):
    
    provider = provider or request.args.get('provider')
    if not provider:
        abort(404, "provider is required")
    
    _provider = queries.get_provider(provider)
    provider_name = _provider["name"]

    query = {"provider_name": provider_name, 
             "enable": True}
    cursor = queries.col_categories().find(query, {"_id": False})
    cursor = cursor.sort([("position", 1), ("category_code", 1)])
    
    categories = OrderedDict([(doc["category_code"], doc ) for doc in cursor])
    ds_codes = []
    
    for_remove = []
    for cat in categories.values():
        if cat.get("parent"):
            parent = categories[cat.get("parent")]
            if not "children" in parent:
                parent["children"] = []
            parent["children"].append(cat)
            for_remove.append(cat["category_code"])
        if cat.get("datasets"):
            for ds in cat.get("datasets"):
                ds_codes.append(ds["dataset_code"])

    for r in for_remove:
        categories.pop(r)
        
    ds_query = {'provider_name': provider_name,
                "enable": True,
                "dataset_code": {"$in": list(set(ds_codes))}}
    ds_projection = {"_id": True, "dataset_code": True, "slug": True}
    cursor = queries.col_datasets().find(ds_query, ds_projection)
    
    dataset_codes = {}
    for doc in cursor:
        dataset_codes[doc['dataset_code']] = {
            "slug": doc['slug'],
            "url": url_for('views.ajax-dataset-by-slug', slug=doc["slug"])
        }

    is_ajax = request.args.get('json') or request.is_xhr
    
    context = dict(
        provider=_provider,
        categories=categories,
        dataset_codes=dataset_codes
    )
    
    if is_ajax:
        data = render_template("datatree_ajax.html", **context)
        #context["js"] = data
        #response_data = json_tools.json_response(data, return_value=True)
        response = current_app.response_class(data,
                                mimetype='application/javascript')
        response.status_code = 200    
        response.make_conditional(request)
        return response
        

    return render_template('categories.html', **context)

@bp.route('/ajax/series/cart/add', endpoint="ajax-cart-add")
def ajax_cart_add():
    slug = request.args.get('slug')
    cart = session.get("cart", [])
    if not slug in cart:
        cart.append(slug)
        flash("Series add to cart.", "success")
    else:
        flash("Series is already in the cart.", "warning")
        
    session["cart"] = cart
    return current_app.jsonify(dict(count=len(session["cart"])))

@bp.route('/ajax/series/cart/remove', endpoint="ajax-cart-remove")
def ajax_cart_remove():
    slug = request.args.get('slug')
    cart = session.get("cart", [])
    if slug in cart:
        cart.remove(slug)
        flash("Series remove from cart.", "success")
    else:
        flash("Series not in the cart.", "warning")
        
    session["cart"] = cart
    return current_app.jsonify(dict(count=len(session["cart"])))
        
@bp.route('/ajax/series/cart/view', endpoint="ajax-cart-view")
def ajax_cart_view():

    is_ajax = request.args.get('json') #or request.is_xhr
    
    if not is_ajax:
        return render_template("series-cart.html")
    
    datas = {"rows": None, "total": 0}
    
    projection = {
        "dimensions": False, 
        "attributes": False, 
        "release_dates": False,
        "revisions": False,
        "values": False,
    }
    cart = session.get("cart", None)
    
    if cart:
        series_slug = [c for c in cart]
        series = queries.col_series().find({"slug": {"$in": series_slug}},
                                                    projection=projection)
        
        docs = list(series)
        for s in docs:
            s['view'] = url_for('.ajax-series-by-slug', slug=s['slug'])

            dataset_slug = slugify("%s-%s" % (s["provider_name"], 
                                              s["dataset_code"]),
                            word_boundary=False, save_order=True)
        
            s['view_dataset'] = url_for('.ajax-dataset-by-slug', slug=dataset_slug)
            s['dataset_slug'] = dataset_slug
            s['export_csv'] = url_for('download.series_csv', slug=s['slug'])
            #s['view_graphic'] = url_for('.series_plot', slug=s['slug'])
            s['frequency_txt'] = s['frequency']
            if s['frequency'] in constants.FREQUENCIES_DICT:
                s['frequency_txt'] = constants.FREQUENCIES_DICT[s['frequency']]

        datas["rows"] = docs
        
    return current_app.jsonify(datas["rows"])
        
#@cache.cached(timeout=120)
@bp.route('/ajax/tags/prefetch/series', endpoint="ajax-tag-prefetch-series")
def tag_prefetch_series():

    provider = request.args.get('provider')
    if not provider:
        abort(400, "provider is required")
    #dataset = request.args.get('dataset')
    limit = request.args.get('limit', default=200, type=int)
    count = request.args.get('count', default=0, type=int)
    
    col = current_app.widukind_db[constants.COL_TAGS]
    
    """
        "_id" : ObjectId("570cc1cea7ceda5f82ec4624"),
        "name" : "ipch-2015-fr-coicop",
        "enable" : true,
        "count_datasets" : 1,
        "count" : 1,
        "provider_name" : [
                "INSEE"
        ],
        "dataset_code" : [
                "IPCH-2015-FR-COICOP"
        ],
        "datasets" : [
                "insee-ipch-2015-fr-coicop"
        ]
    """
    #TODO: renvoyer aussi count pour tag(count)
    query = OrderedDict()
    query["provider_name"] = {"$in": [provider.upper()]}
    if count:
        query["count"] = {"$gte": count}
    #print("QUERY : ", query)
    #tags = col.distinct("name", query)
    #return current_app.jsonify(tags)
    
    projection = {"_id": False, "name": True, "count": True, "count_datasets": True}
    docs = col.find(query, projection=projection).sort("count", DESCENDING).limit(limit)
    return current_app.jsonify(list(docs))
    
@bp.route('/ajax/explorer/datas', endpoint="ajax-explorer-datas")
def ajax_explorer_datas():

    limit = request.args.get('limit', default=100, type=int)
    if limit > 1000:
        limit = 1000
        
    #7 669 115 octets pour 1000 series
    
    projection = {
        "_id": False,
        "dimensions": False, 
        "attributes": False, 
        "values.revisions": False,
        "notes": False,
        "tags": False
    }
    
    query = OrderedDict()
    provider_slug = request.args.get('provider')
    dataset_slug = request.args.get('dataset')
    search = request.args.get('search')
    
    if dataset_slug:
        dataset = queries.get_dataset(dataset_slug)
        query["provider_name"] = dataset["provider_name"]
        #query["dataset_code"] = dataset["dataset_code"]
    elif provider_slug:
        provider = queries.get_provider(provider_slug)
        query["provider_name"] = provider["name"]
    
    if search:
        query["$text"] = {"$search": search}
        projection['score'] = {'$meta': 'textScore'}
        
    if dataset_slug:
        query["dataset_code"] = dataset["dataset_code"]
    else:
        ds_enabled_query = {"enable": False}
        if "provider_name" in query:
            ds_enabled_query["provider_name"] = query["provider_name"]
        disabled_datasets = [doc["dataset_code"] for doc in queries.col_datasets().find(ds_enabled_query, 
                                                                                        {"dataset_code": True})]
        
        query["dataset_code"] = {"$nin": disabled_datasets}
    
    query = complex_queries_series(query)
    cursor = queries.col_series().find(dict(query), projection).limit(limit)
    #cursor.limit(limit)
    if search:
        cursor = cursor.sort([('score', {'$meta': 'textScore'})])

    count = cursor.count()
    
    series_list = [doc for doc in cursor]

    rows = []
    
    for s in series_list:
        s['start_date'] = s["values"][0]["period"]
        s['end_date'] = s["values"][-1]["period"]
        values = [{"period": v["period"], "value": v["value"]} for v in s['values']]
        del s["values"]
        s["values"] = values

        s['view'] = url_for('.ajax-series-by-slug', slug=s['slug'])

        dataset_slug = slugify("%s-%s" % (s["provider_name"], 
                                          s["dataset_code"]),
                        word_boundary=False, save_order=True)
        
        s['view_dataset'] = url_for('.ajax-dataset-by-slug', slug=dataset_slug)
        s['dataset_slug'] = dataset_slug
        s['export_csv'] = url_for('download.series_csv', slug=s['slug'])
        #s['view_graphic'] = url_for('.series_plot', slug=s['slug'])
        #TODO: s['url_dataset'] = url_for('.dataset', id=s['_id'])
        s['frequency_txt'] = s['frequency']
        if s['frequency'] in constants.FREQUENCIES_DICT:
            s['frequency_txt'] = constants.FREQUENCIES_DICT[s['frequency']]
        
        rows.append(s)

    return json_tools.json_response(rows, {"total": count})

@bp.route('/explorer', endpoint="explorer")
@bp.route('/explorer/<provider>', endpoint="explorer_p")
@bp.route('/explorer/<provider>/<dataset>', endpoint='explorer_p_d')
@bp.route('/explorer/dataset/<dataset>', endpoint='explorer_d')
@bp.route('/', endpoint="home")
def explorer_view(provider=None, dataset=None):
    """
    http://127.0.0.1:8081/views    
    http://127.0.0.1:8081/views/explorer    
    http://127.0.0.1:8081/views/explorer/insee    
    http://127.0.0.1:8081/views/explorer/insee/insee-cna-2005-ere-a88    
    """
    if dataset:
        doc = queries.get_dataset(dataset)
        provider_doc = queries.col_providers().find_one({"name": doc["provider_name"]},
                                                        {"slug": True})
        provider = provider_doc["slug"]
    ctx = {
        "selectedProvider": provider,
        "selectedDataset": dataset
    }
    return render_template("series-home.html", **ctx)

@bp.route('/datasets/last-update.html', endpoint="datasets-last-update")
def datasets_last_update():
    
    is_ajax = request.args.get('json') or request.is_xhr
    if not is_ajax:
        return render_template("datasets-last-update.html")

    query = {
        "$or": [{"count_inserts": {"$gt": 0}}, {"count_updates": {"$gt": 0}}]
    }

    startDate = arrow.utcnow().replace(days=-1).floor("second")
    print("startDate : ", startDate, startDate.datetime)
    query["created"] = {"$gte": startDate.datetime}
    
    limit = request.args.get('limit', default=0, type=int)
    
    cursor = queries.col_stats_run().find(query)
    if limit:
        cursor = cursor.limit(limit)    
    count = cursor.count()
    cursor = cursor.sort("created", -1)
    rows = [doc for doc in cursor]
    for row in rows:
        slug = slugify("%s-%s" % (row["provider_name"], row["dataset_code"]),
                        word_boundary=False, save_order=True)
        row["view"] = url_for(".explorer_d", dataset=slug)
    
    return json_tools.json_response(rows, {"total": count})

def home_views(bp_or_app):

    @bp_or_app.route('/', endpoint="home")
    @cache.cached(timeout=360)
    def index():
    
        cursor = queries.col_providers().find({}, {"metadata": False})
        providers = [doc for doc in cursor]
        
        total_datasets = 0
        total_series = 0
        datas = {}
        for provider in providers:
            provider["count_datasets"] = queries.col_datasets().count({"provider_name": provider["name"]})
            provider["count_series"] = queries.col_series().count({"provider_name": provider["name"]})
            datas[provider["slug"]] = provider
            total_datasets += provider["count_datasets"]
            total_series += provider["count_series"]
    
        return render_template("index.html", 
                               providers=datas, 
                               total_datasets=total_datasets,
                               total_series=total_series)
        
    @bp_or_app.route('/rss.xml', endpoint="rss")
    def atom_feed():
        from werkzeug.contrib.atom import AtomFeed
        now = arrow.utcnow().datetime
        feed = AtomFeed("Widukind", 
                        feed_url=request.url,
                        #url=request.host_url,
                        #updated=now,
                        subtitle="Updated datasets - Last 24 hours"
                        )
    
        query = {
            "$or": [{"count_inserts": {"$gt": 0}}, {"count_updates": {"$gt": 0}}]
        }
    
        startDate = arrow.utcnow().replace(days=-1).floor("second")
        print("startDate : ", startDate, startDate.datetime)
        query["created"] = {"$gte": startDate.datetime}
        
        limit = request.args.get('limit', default=0, type=int)
        
        cursor = queries.col_stats_run().find(query)
        if limit:
            cursor = cursor.limit(limit)    
    
        cursor = cursor.sort("created", -1)
        rows = [doc for doc in cursor]
        
        slugs = []
        
        for row in rows:
            slug = slugify("%s-%s" % (row["provider_name"], row["dataset_code"]),
                            word_boundary=False, save_order=True)
            slugs.append((row, slug))
            
        query_dataset = {"slug": {"$in": [s[1] for s in slugs]}}
        projection = {"metadata": False, "concepts": False, "codelists": False}
        datasets = {doc["slug"]: doc for doc in queries.col_datasets().find(query_dataset, projection)}
        
        for row, slug in slugs:
            dataset = datasets.get(slug)
            if not dataset["enable"]:
                continue
            
            url = url_for("views.explorer_d", dataset=slug, _external=True)
            #content = """
            #<p>Updated date from provider : %(last_update)s</p>
            #"""
            
            feed.add(title="%s - %s" % (row["provider_name"], row["dataset_code"]), 
                     summary=dataset["name"],
                     #content=content % dataset,
                     #content_type="html", 
                     url=url, 
                     id=slug,
                     updated=row["created"], #dataset["last_update"], 
                     published=dataset["download_last"]
                     )
        
        return feed.get_response()
        

