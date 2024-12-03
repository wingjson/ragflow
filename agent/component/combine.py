

#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
from abc import ABC
import json

from agent.component.base import ComponentBase, ComponentParamBase

class CombineParam(ComponentParamBase):
    """
    Define the Crawler component parameters.
    """

    def __init__(self):
        super().__init__()
        self.parameters = []
    
    def check(self):
        return True


class Combine(ComponentBase, ABC):
    component_name = "Combine"

    def _run(self, history, **kwargs):
        res = {}
        for para in self._param.parameters:
            cpn = self._canvas.get_component(para["component_id"])["obj"]
            _, out = cpn.output(allow_partial=False)
            if "content" not in out.columns:
                res[para["key"]] = "Nothing"
            else:
                res[para["key"]] = "  - " + "\n  - ".join(out["content"])

        return Combine.be_output(json.dumps(res, ensure_ascii=False))