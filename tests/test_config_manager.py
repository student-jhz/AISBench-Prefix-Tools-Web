"""ConfigManager 核心逻辑测试"""

from app.core import config_manager as cm


class TestGenerate:
    def test_generate_full_config(self):
        content = cm.generate_config_content({
            'dataset_path': '/benchmark/dataset',
            'model_name': 'ds',
            'model_path': '/mnt/models/ds',
            'host_ip': '10.0.0.1',
            'host_port': '8000',
            'api_key': '',
            'pod_info': '10.0.0.1:8000,10.0.0.2:8000',
        }, dataset_path_default='/benchmark/ais_bench/aisbench_auto_tools_prefix-main')
        assert 'DATASET_PATH = "/benchmark/dataset"' in content
        assert 'MODEL_NAME = "ds"' in content
        assert 'MODEL_PATH = "/mnt/models/ds"' in content
        assert 'HOST_IP = "10.0.0.1"' in content
        assert 'HOST_PORT = "8000"' in content
        assert 'POD_INFO = ["10.0.0.1:8000", "10.0.0.2:8000"]' in content

    def test_dataset_path_default(self):
        content = cm.generate_config_content(
            {}, dataset_path_default='/benchmark/ais_bench/aisbench_auto_tools_prefix-main')
        assert 'DATASET_PATH = "/benchmark/ais_bench/aisbench_auto_tools_prefix-main"' in content

    def test_pod_info_list(self):
        content = cm.generate_config_content({'pod_info': ['1.1.1.1:8000']})
        assert "POD_INFO = ['1.1.1.1:8000']" in content


class TestValidate:
    def test_valid(self):
        errors = cm.validate_config({
            'model_name': 'ds', 'model_path': '/m',
            'host_ip': '1.2.3.4', 'host_port': '8000',
        })
        assert errors == []

    def test_missing_fields(self):
        errors = cm.validate_config({})
        assert any('MODEL_NAME' in e for e in errors)
        assert any('MODEL_PATH' in e for e in errors)
        assert any('HOST_IP' in e for e in errors)
        assert any('HOST_PORT' in e for e in errors)

    def test_port_not_number(self):
        errors = cm.validate_config({
            'model_name': 'ds', 'model_path': '/m',
            'host_ip': '1.2.3.4', 'host_port': 'abc',
        })
        assert any('HOST_PORT' in e for e in errors)

    def test_pod_info_bad_format(self):
        errors = cm.validate_config({
            'model_name': 'ds', 'model_path': '/m',
            'host_ip': '1.2.3.4', 'host_port': '8000',
            'pod_info': '1.1.1.1',
        })
        assert any('POD_INFO' in e for e in errors)


class TestParse:
    def test_parse_roundtrip(self):
        content = cm.generate_config_content({
            'model_path': '/mnt/models/ds',
            'host_ip': '10.0.0.1',
            'pod_info': '10.0.0.1:8000',
        })
        parsed = cm.parse_config_content(content)
        assert parsed['MODEL_PATH'] == '/mnt/models/ds'
        assert parsed['HOST_IP'] == '10.0.0.1'
        assert '10.0.0.1:8000' in parsed['POD_INFO']

    def test_compare_config(self):
        config = {'model_path': '/a', 'host_ip': '1.1.1.1'}
        remote = cm.generate_config_content({'model_path': '/b', 'host_ip': '1.1.1.1'})
        mismatches, saved = cm.compare_config(config, remote)
        assert len(mismatches) == 1
        assert 'MODEL_PATH' in mismatches[0]
        assert saved['HOST_IP'] == '1.1.1.1'

    def test_compare_config_match(self):
        config = {'model_path': '/a', 'host_ip': '1.1.1.1'}
        remote = cm.generate_config_content(config)
        mismatches, _ = cm.compare_config(config, remote)
        assert mismatches == []
