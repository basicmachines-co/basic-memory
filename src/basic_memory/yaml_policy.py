"""One tree-only YAML policy for note metadata and schema frontmatter."""

from typing import Any, override

import yaml
from frontmatter.default_handlers import YAMLHandler
from yaml.events import AliasEvent
from yaml.nodes import Node


class SimpleSafeLoader(yaml.SafeLoader):
    """Reject aliases and merge keys before construction can expand a graph."""

    @override
    def compose_node(self, parent: Node | None, index: object) -> Node:
        if self.check_event(AliasEvent):
            raise yaml.YAMLError("YAML aliases are not supported in note frontmatter")
        node = super().compose_node(parent, index)
        if node.tag == "tag:yaml.org,2002:merge":
            raise yaml.YAMLError("YAML merge keys are not supported in note frontmatter")
        return node


class SimpleSafeDumper(yaml.SafeDumper):
    """Write repeated values independently, without introducing YAML aliases."""

    @override
    def ignore_aliases(self, data: object) -> bool:
        return True


class SimpleYamlHandler(YAMLHandler):
    """Apply the same mandatory loader policy through python-frontmatter."""

    @override
    def load(self, fm: str, **kwargs: object) -> Any:
        return yaml.load(fm, Loader=SimpleSafeLoader)
