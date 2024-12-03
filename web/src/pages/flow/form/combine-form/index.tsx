import { Form } from 'antd';
import { useSetLlmSetting } from '../hooks';
import { IOperatorForm } from '../interface';
import DynamicParameters from './dynamic-parameters';

const CombineForm = ({ onValuesChange, form, node }: IOperatorForm) => {
  useSetLlmSetting(form);

  return (
    <Form
      name="basic"
      labelCol={{ span: 10 }}
      wrapperCol={{ span: 14 }}
      autoComplete="off"
      form={form}
      onValuesChange={onValuesChange}
    >
      <DynamicParameters nodeId={node?.id}></DynamicParameters>
    </Form>
  );
};

export default CombineForm;
