from urllib.parse import urlencode

from django import template

from netatlas.tagging.services import list_tags, tags_of

register = template.Library()


@register.simple_tag(takes_context=True)
def item_tags(context, kind, item_id):
    return tags_of(context["request"].user, kind, item_id)


@register.inclusion_tag("netatlas/_tag_filter.html", takes_context=True)
def tag_filter(context):
    """Links that switch each Tag on or off in the current list's filter."""
    request = context["request"]
    chosen = [name for name in request.GET.getlist("tag") if name.strip()]
    chosen_keys = {name.strip().casefold() for name in chosen}
    links = []
    for tag in list_tags(request.user):
        is_active = tag.name.casefold() in chosen_keys
        if is_active:
            names = [name for name in chosen if name.strip().casefold() != tag.name.casefold()]
        else:
            names = [*chosen, tag.name]
        query = urlencode([("tag", name) for name in names])
        url = f"{request.path}?{query}" if query else request.path
        links.append({"name": tag.name, "url": url, "is_active": is_active})
    return {"links": links}
