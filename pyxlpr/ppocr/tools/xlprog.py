#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# @Author : 陈坤泽
# @Email  : 877362867@qq.com
# @Date   : 2022/02/21 11:07

"""
对PaddleOcr进行了更高程度的工程化封装
"""

import os
import sys
import yaml
import shutil
import copy
import inspect
import math

import numpy as np

from pyxlpr.ppocr.tools.program import preprocess
from pyxlpr.ppocr.data import build_dataloader

from pyxllib.algo.geo import rect_bounds, ltrb2xywh
from pyxllib.file.specialist import XlPath, ensure_localfile, ensure_localdir
from pyxllib.cv.xlcvlib import xlcv
from pyxllib.prog.newbie import round_int


class PaddleOcrBaseConfig:
    """ paddle(ocr)标准配置文件的封装，为了简化配置方便自己使用，
    做了一个中间层组件，方便做一些统一的参数设置、修改
    """

    def __init__(self):
        self.cfg = {}

    def __1_config(self):
        """ 配置文件相关的功能 """
        pass

    def autoset(self):
        """ 这个接口方便写一些通用的配置 """

        x = self.cfg['Global']
        x['use_visualdl'] = True
        x['print_batch_step'] = 1000  # 这个单位是iter。原本很小2，我改成了100。但epoch很小的时候，每轮epoch也会输出。
        x['pretrained_model'] = None
        # 每隔多少次epoch，保存模型，原本默认是1200，这里故意设置的特别大，相当于不保存模型，需要的话手动设置补充。
        # 虽然没有固定间隔保存模型，但默认还是会根据eval，保存最优模型的
        x['save_epoch_step'] = 100000

        self.set_save_dir('models/' + inspect.stack()[3].function)

    def resume(self, train=False):
        """ 如果没有设置checkpoints，尝试加载best_accuracy或latest模型

        跟是否是Train模式有关，默认加载的模型会不一样
            train要加载latest，其他默认优先加载accuracy
        """
        if train:
            candidates = ['latest', 'best_accuracy']
        else:
            candidates = ['best_accuracy', 'latest']

        for name in candidates:
            f = XlPath(self.cfg['Global']['save_model_dir']) / name
            if f.with_suffix('.pdparams').exists():
                self.cfg['Global']['checkpoints'] = f
                return

    def config_from_content(self, content):
        self.cfg = yaml.safe_load(content)
        self.autoset()
        return self.cfg

    def config_from_template(self, subpath):
        """
        :param subpath: 例如 'det/det_mv3_db'
        """
        f = os.path.join(sys.modules['pyxlpr.ppocr'].__path__[0], 'configs', subpath + '.yml')
        return self.config_from_content(XlPath(f).read_text())

    def set_save_dir(self, save_dir):
        """ 有很多个运行中文件的输出路径，可以统一到一个地方，并且只设置一次就够 """
        # self.d['Global']
        save_dir = XlPath(save_dir)
        x = self.cfg['Global']
        x['save_model_dir'] = save_dir  # train时模型存储目录
        x['save_inference_dir'] = save_dir / 'infer'  # export_model时存储目录
        # 这个选项暂时还不清楚具体作用，不知道是不是db专有的
        x['save_res_path'] = save_dir / 'predicts.txt'

    def set_simpledataset(self, mode, data_dir, label_file_list, ratio_list=None):
        """ paddle官方标准的SimpleDataset数据格式

        :param str mode: Train or Eval，设置训练集或者验证集
        :param PathLike data_dir: 数据所在根目录
        :param list label_file_list: 标注文件清单 [txtfile1, textfile2, ...]
            每个txtfile文件里的内容，每行是一张图的标注
            每行第1列是图片相对data_dir的路径，\t隔开，第2列是json.dumps的json标注数据
            json里有transcription字段存储文本内容，points存储四边形框位置
        :param list ratio_list: 只有一个label_file_list的时候，可以只输入一个数字，但最好统一按列表输入
            填写一个0~1.0的小数值，表示所取样本比例数
            这个paddle官方实现是随机取的，没有顺序规律
        """
        # 注意如果在SimpleDataSet、XlSimpleDataSet之间切换的话，有些字段格式是有区别的
        # 保险起见，就把self.cfg[mode]['dataset']整个重置了
        node = self.cfg[mode]['dataset']
        x = {'name': 'SimpleDataSet',
             'data_dir': XlPath(data_dir),
             'label_file_list': label_file_list}
        if ratio_list:
            x['ratio_list'] = ratio_list
        x['transforms'] = node['transforms']
        self.cfg[mode]['dataset'] = x

    def set_xlsimpledataset(self, mode, data_dir, data_list):
        """ 设置自己的XlSampleDataSet数据格式

        用于对各种源生的格式，在程序运行中将格式直接转为paddle的内存支持格式接口，从而不用重复生成冗余的中间数据文件
        目前最主要的是扩展了对xllabelme标注格式的支持，如labelme_det

        :param str mode: Train or Eval，设置训练集或者验证集
        :param PathLike data_dir: 数据所在根目录
        :param list data_list: 数据具体清单，每个条目都是一个字典
            [必填]type: 具体的数据格式，目前支持 labelme_det, icdar2015, refineAgree
                具体支持的方法，可以见XlSimpleDataSet类下前缀为from_的成员方法
            其他为选填字段，具体见from_定义支持的扩展功能，一般有以下常见参数功能
            [ratio] 一个小数比例，可以负数代表从后往前取
                一般用于懒的物理区分Train、Eval数据集的时候，在代码里用算法自动拆分训练、验证集

        """
        node = self.cfg[mode]['dataset']
        x = {'name': 'XlSimpleDataSet',
             'data_dir': XlPath(data_dir),
             'data_list': data_list}
        x['transforms'] = node['transforms']
        self.cfg[mode]['dataset'] = x

    @classmethod
    def _rset_posix_path(cls, d):
        from pathlib import Path

        if isinstance(d, list):
            for i, x in enumerate(d):
                if isinstance(x, (Path, XlPath)):
                    d[i] = x.as_posix()
                else:
                    cls._rset_posix_path(x)
        elif isinstance(d, dict):
            for k, v in d.items():
                if isinstance(v, (Path, XlPath)):
                    d[k] = v.as_posix()
                else:
                    cls._rset_posix_path(v)

    def rset_posix_path(self):
        """ 配置字典中，可能存在XlPath、Path类，需要递归统一修改为str类型来存储

        rset是递归设置的意思
        """
        d = copy.deepcopy(self.cfg)
        self._rset_posix_path(d)
        return d

    def write_cfg_tempfile(self):
        """ 存储一个文件到临时目录，并返回文件路径 """
        p = XlPath.tempfile('.yml')
        # TODO 写入文件前，会把配置里 XlPath全部转为 as_poisx 的str
        self._rset_posix_path(self.cfg)
        p.write_yaml(self.cfg)
        return str(p)

    def add_config_to_cmd_argv(self):
        """ 把配置参数加入命令行的 -c 命令中 """
        sys.argv = sys.argv + ['-c', self.write_cfg_tempfile()]

    def set_iter_num(self, num):
        """ 按迭代数设置训练长度

        paddle的配置源生并不支持按iter来统计训练长度，
        要通过batch_size_per_card和数据量，来反推epoch_num需要设置多少

        注意要先设置好数据集，再继续迭代数哦！
        """
        config, device, logger, _ = preprocess(from_dict=self.rset_posix_path(), use_visualdl=False)
        train_dataloader = build_dataloader(config, 'Train', device, logger)
        per_epoch_iter_num = len(train_dataloader)  # 每个epoch的迭代数
        self.cfg['Global']['epoch_num'] = math.ceil(num / per_epoch_iter_num)

    def __2_main(self):
        """ 一些脚本功能工具 """
        pass

    def train(self, resume=False):
        from pyxlpr.ppocr.tools.train import main

        if resume:
            self.resume(True)
        config, device, logger, vdl_writer = preprocess(is_train=True, from_dict=self.rset_posix_path())
        main(config, device, logger, vdl_writer)

    def eval(self, resume=True, *, dataset_mode='Eval'):
        """
        :param dataset_mode: 使用的数据集，默认是Eval，也可以用Train
        """
        from pyxlpr.ppocr.tools.eval import main

        if resume:
            self.resume()

        config, device, logger, vdl_writer = preprocess(from_dict=self.rset_posix_path())
        for k in ['name', 'data_dir', 'data_list']:
            config['Eval']['dataset'][k] = config[dataset_mode]['dataset'][k]
        metric = main(config, device, logger)
        return metric

    def infer_det(self, resume=True):
        from pyxlpr.ppocr.tools.infer_det import main

        if resume:
            self.resume()
        config, device, logger, vdl_writer = preprocess(from_dict=self.rset_posix_path())
        main(config, logger)

    def export_model(self, resume=True):
        from pyxlpr.ppocr.tools.export_model import main

        if resume:
            self.resume()
        config, device, logger, vdl_writer = preprocess(from_dict=self.rset_posix_path())
        main(config, logger)

    def __3_pretrained(self):
        """ 使用预训练模型相关配置的封装 """

    @classmethod
    def get_pretrained_model_backbone(cls, name):
        """ 只拿骨干网络的权重 """
        local_file = XlPath.userdir() / f'.paddleocr/pretrained/{name}.pdparams'
        url = f'https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/{name}.pdparams'
        ensure_localfile(local_file, url)
        return local_file.parent / local_file.stem  # 省略.pdparams后缀

    @classmethod
    def get_pretrained_model_ppocr(cls, name):
        local_dir = XlPath.userdir() / f'.paddleocr/pretrained/{name}'
        url = f'https://paddleocr.bj.bcebos.com/dygraph_v2.0/ch/{name}.tar'
        ensure_localdir(local_dir, url, wrap=-1)
        return local_dir / 'best_accuracy'  # ppocr训练好的ocr模型

    def set_pretrained_model_backbone(self, name):
        path = self.get_pretrained_model_backbone(name)
        self.cfg['Global']['pretrained_model'] = path

    def set_pretrained_model_ppocr(self, name):
        path = self.get_pretrained_model_ppocr(name)
        self.cfg['Global']['pretrained_model'] = path

    def set_pretrained_infer_model(self, local_dir, url):
        """ 自己扩展的一个配置参数，metric的时候用 """
        local_dir = XlPath.userdir() / f'.paddleocr/pretrained_infer/{local_dir}'
        path = ensure_localdir(local_dir, url, wrap=-1)
        self.cfg['Global']['pretrained_infer_model'] = path

    def set_pretrained_model(self, pretrained, models):
        """ 对上述功能进一步封装，简化高层接口配置时的代码复杂度

        :param bool|int pretrained:
            0 不使用预训练权重
            1 使用骨干网络权重
            2 使用完整的ppocr权重
            3 之前定制训练过的最好的模型
        :param models: pretrained为1、2时加载的模型
        """
        if pretrained == 1:
            self.set_pretrained_model_backbone(models[0])
        elif pretrained == 2:
            self.set_pretrained_model_ppocr(models[1])
        elif pretrained == 3:
            self.cfg['Global']['pretrained_model'] = self.cfg['Global']['save_model_dir'] / 'best_accuracy'

    def __call__(self, *args, **kwargs):
        # 让fire库配合return self不会报错
        pass


class XlDet(PaddleOcrBaseConfig):
    """ 检测模型专用配置
    """

    def autolabel(self, datadir='data', *, model_type=0, **kwargs):
        """ 预标注检测、识别

        TODO model_type在det1_mobile的时候，默认设为2？
        """
        pocr = self.build_ppocr(model_type, **kwargs)
        pocr.ocr2labelme(datadir, det=True, rec=True)

    def set_deploy_args_det(self):
        """ 检测模型在部署时候的参数，不一定跟eval一样
        换句话说，eval本来就应该尽量贴近真实部署的配置参数

        由于很多文本检测的配置文件，在eval时有些配置跟部署不同，这里按照deploy的情况自动进行调整

        当然，有些配置，如果eval效果确实比deploy来的好，可以考虑deploy采用eval的配置方式
        """
        for x in self.cfg['Eval']['dataset']['transforms']:
            if 'DetResizeForTest' in x:
                x['DetResizeForTest'] = {'limit_side_len': 960, 'limit_type': 'max'}

    def det1_mobile_init(self, *, pretrained=2):
        """
        官方实验:ic15, train1000+val500张, batch_size_per_card=8, epoch=1200
            也就是总训练量120w，除batchsize，是15万iter
            按照核酸的实验，每iter耗时大概是0.4秒，实验总用时15iter/3600*0.4约等于17小时

        batchsize=8，hesuan训练过程占用显存 6.7G
            以后有其他实验数据，会尝试都覆盖上，但记忆中差不多都是消耗这么多

        这个部署文件共 3M

        TODO datalist不只一个的相关功能，还没有进行测试，但问题应该不大
        """
        # 1 加载基础的配置
        cfg = self.config_from_template('det/ch_ppocr_v2.0/ch_det_mv3_db_v2.0')
        self.set_pretrained_model(pretrained, ['MobileNetV3_large_x0_5_pretrained', 'ch_ppocr_mobile_v2.0_det_train'])
        self.set_deploy_args_det()

        # 2 预训练权重也提供一个部署模型，供后续metric分析
        infer_model_url = 'https://paddleocr.bj.bcebos.com/dygraph_v2.0/ch/ch_ppocr_mobile_v2.0_det_infer.tar'
        self.set_pretrained_infer_model('ch_ppocr_mobile_v2.0_det_infer', infer_model_url)

    def det1_server_init(self, *, pretrained=2):
        """
        训练显存 10.2 G

        这个部署文件共 47M
        """
        # 1 加载基础的配置
        cfg = self.config_from_template('det/ch_ppocr_v2.0/ch_det_res18_db_v2.0')
        self.set_pretrained_model(pretrained, ['ResNet18_vd_pretrained', 'ch_ppocr_server_v2.0_det_train'])
        self.set_deploy_args_det()

        # 2 预训练权重也提供一个部署模型，供后续metric分析
        infer_model_url = 'https://paddleocr.bj.bcebos.com/dygraph_v2.0/ch/ch_ppocr_server_v2.0_det_infer.tar'
        self.set_pretrained_infer_model('ch_ppocr_server_v2.0_det_infer', infer_model_url)

    def det2_init(self, *, pretrained=1):
        """ 2021.9.7发布的PP-OCRv2
        但是我还没有试跑过，不确定我这样配置是对的

        220223周三18:11，跑通了，但还没有完全对，metric结果有点奇怪，摸不着头脑
        """
        cfg = self.config_from_template('det/ch_PP-OCRv2/ch_PP-OCRv2_det_distill')
        if pretrained:
            x = cfg['Architecture']['Models']

            # self.set_pretrained_model_ppocr('ch_PP-OCRv2_det_distill_train')
            x['Student']['pretrained'] = self.get_pretrained_model_backbone('MobileNetV3_large_x0_5_pretrained')
            # x['Student']['pretrained'] = self.get_pretrained_model_ppocr('ch_PP-OCRv2_det_distill_train')
            x['Teacher']['pretrained'] = self.get_pretrained_model_ppocr('ch_ppocr_server_v2.0_det_train')

        self.set_deploy_args_det()

        infer_model_url = 'https://paddleocr.bj.bcebos.com/PP-OCRv2/chinese/ch_PP-OCRv2_det_infer.tar'
        self.set_pretrained_infer_model('ch_PP-OCRv2_det_infer', infer_model_url)

        return self

    def build_ppocr(self, model_type=2, **kwargs):
        """ 获得部署用的接口类
        导出部署模型，并加载

        :param model_type:
            0，原始的PaddleOcr
            1，配置文件自带的部署文件（需要配置Global.pretrained_infer_model参数）
            2，finetune后的模型
        :param kwargs: 可以增加一些检测模型的配置参数，比如常用的 det_db_unclip_ratio=1.5
        """
        from pyxlpr.paddleocr import PaddleOCR

        if model_type == 0:
            ppocr = PaddleOCR.build_ppocr(**kwargs)
        elif model_type == 1:
            d = self.cfg['Global']['pretrained_infer_model']
            if not d:
                return {}
            ppocr = PaddleOCR.build_ppocr(det_model_dir=d, **kwargs)
        else:
            self.export_model(True)
            ppocr = PaddleOCR.build_ppocr(det_model_dir=self.cfg['Global']['save_inference_dir'], **kwargs)

        return ppocr

    def _build_dataset(self, config, logger, dataset_mode='Eval'):
        from pyxlpr.ppocr.data import build_dataset
        # 注意这里数据集切换方法跟PaddleOCRConfig.eval有点不太一样，因为部署操作要连transforms一起改掉
        src = config[dataset_mode]['dataset']
        config['Eval']['dataset'] = {'name': src['name'],
                                     'data_dir': src['data_dir'],
                                     'data_list': src['data_list'],
                                     'transforms': [{'DetLabelEncode': None}]}
        dataset = build_dataset(config, 'Eval', logger)
        return config, dataset

    def eval_deploy(self, model_type=2, dataset_mode='Eval', **kwargs):
        ppocr = self.build_ppocr(model_type, **kwargs)
        config, device, logger, vdl_writer = preprocess(from_dict=self.rset_posix_path())
        config, dataset = self._build_dataset(config, logger, dataset_mode)
        metric = ppocr.det_metric(dataset)
        logger.info(str(metric))
        return metric

    def metric(self, *, print_mode=False):
        """ 得到一个综合的测评结果，一般如下：
                   type train_dataset eval_dataset
           ①PaddleOCR*      32.35*56    100.0*190
           ②pretrained      17.57*43      50.0*22
          ③pretrained*     17.57*184     50.0*192
             ④finetune      93.05*49     100.0*20
            ⑤finetune*     93.05*173    100.0*164

        有几条规律
        1、精度②=③，速度③>②。如果精度不同，很可能官方给的预训练模型和部署文件有问题
        2、精度④=⑤，速度⑤>④。如果精度不同，可能eval和部署阶段的图片处理方式不同
            det就存在这个问题，处理后的图片尺寸不同，通过set_deploy_args_det修复了
        3、去掉上述两个eval阶段的，其实就是①③⑤三个模型间的比较
            ①PaddleOCR直接给的一条龙准备好的模型，一般要高于③开源模型训练效果，低于⑤定制化的效果
            即精读：③<①<⑤
        """
        import pandas as pd
        from pyxllib.algo.stat import xlpivot

        # 1 收集各模型结果
        eval_list = []

        def core(title, eval_func):
            for dataset in ['a、Train', 'b、Eval']:
                m = eval_func(dataset[2:])  # m, metric
                m = {k: (round_int(v) if k in ('fps', 'total_frame') else round(v * 100, 2)) for k, v in m.items()}
                m2 = {'model_type': title, 'dataset': dataset}
                m2.update(m)
                eval_list.append(m2)

        core('①PaddleOCR*', lambda m: self.eval_deploy(model_type=0, dataset_mode=m))
        core('②pretrained', lambda m: self.eval(resume=False, dataset_mode=m))
        core('③pretrained*', lambda m: self.eval_deploy(model_type=1, dataset_mode=m))
        core('④finetune', lambda m: self.eval(resume=True, dataset_mode=m))
        core('⑤finetune*', lambda m: self.eval_deploy(model_type=2, dataset_mode=m))

        # 2 最后的统计表
        df = pd.DataFrame.from_records(eval_list)
        outfile = self.cfg['Global']['save_model_dir'] / f'results/metric.html'
        os.makedirs(outfile.parent, exist_ok=True)

        def func(items):
            x = items.iloc[0]
            return f'{x["precision"]:.0f}，{x["recall"]:.0f}，{x["hmean"]:.2f}，{x["fps"]}'

        df2 = xlpivot(df, ['model_type'], ['dataset', 'total_frame'], {'precision，recall，hmean，fps': func})
        stat_html = df2.to_html()
        stat_html = stat_html.replace('<th></th>', f'<th>{sys.argv[2]}</th>', 1)
        outfile.write_text(stat_html)

        if 'metric' in sys.argv:
            print(df2)
            return

        if print_mode:
            print(df2)

        return df

    def create_visual_results(self, *, model_type=2, max_samples=None, **kwargs):
        """ 将可视化结果生成到目录下

        :param max_samples: 限制生成的可视化图片上限，有时候只需要看少量样本

        【算法流程】基本思路是将数据转成coco格式后，使用coco的功能接口来实现，考验我以前接口好不好用的时候到了
        1、初始化指定的ppocr
        2、用ppocr生成一套检测结果
        3、和gt对比，生成一套coco数据
        4、生成coco可视化结果
        5、生成coco的数据分析表格
        """
        import PIL.Image
        from pyxlpr.data.coco import CocoGtData, CocoMatch

        ppocr = self.build_ppocr(model_type, **kwargs)
        for dataset_mode in ['Train', 'Eval']:  # 训练集和验证集结果都生成，放在两个不同目录
            gt = {'images': [],
                  'annotations': [],
                  'categories': CocoGtData.gen_categories(['text'])}
            dt = []
            k = 1

            config, device, logger, vdl_writer = preprocess(from_dict=self.rset_posix_path())
            config, dataset = self._build_dataset(config, logger, dataset_mode)
            out_dir = self.cfg['Global']['save_model_dir'] / f'results/{dataset_mode}'
            data_dir = self.cfg['Eval']['dataset']['data_dir']
            for img_id, x in enumerate(dataset, start=1):
                if max_samples and img_id > max_samples:
                    break

                # 1 拷贝图片数据到相对目录
                src_img_path = x['img_path']
                rel_img_path = XlPath(src_img_path).relpath(data_dir)
                dst_img_path = out_dir / rel_img_path
                os.makedirs(dst_img_path.parent, exist_ok=True)
                if not dst_img_path.is_file():
                    shutil.copy2(src_img_path, dst_img_path)

                # 2 生成对应图片的gt标注数据
                w, h = PIL.Image.open(str(dst_img_path)).size
                gt['images'].append(CocoGtData.gen_image(img_id, rel_img_path, h, w))
                for p in x['polys']:
                    gt['annotations'].append(
                        CocoGtData.gen_annotation(id=k, image_id=img_id, points=p, text=x['texts']))
                    k += 1

                # 3 生成dt标注数据
                img = xlcv.read_from_buffer(x['image'])
                for p in ppocr.ocr(img, rec=False):
                    dt.append({'image_id': img_id, 'category_id': 1, 'segmentation': np.array(p).reshape([1, -1]),
                               'bbox': ltrb2xywh(rect_bounds(p)), 'score': 1.0})

            cm = CocoMatch(gt, dt)
            cm.to_labelme_match(out_dir, segmentation=True)
            cm.to_excel(out_dir / 'cocomatch.xlsx')

    def __config_demo(self):
        """ 常用的配置示例 """

    def det1_mobile_raw(self):
        """ paddle源生格式的配置示例 """
        self.det1_mobile_init(pretrained=2)  # 基础配置
        self.set_save_dir('models/det1_mobile_raw')  # 模型保存位置
        self.set_simpledataset('Train', 'data', ['data/ppdet_train.txt'])
        self.set_xlsimpledataset('Eval', 'data', ['data/ppdet_val.txt'])
        self.set_iter_num(150000)
        return self

    def det1_mobile(self):
        """ labelme标注格式的检测训练 """
        self.det1_mobile_init(pretrained=2)  # 基础配置
        self.set_save_dir('models/det1_mobile')  # 模型保存位置
        self.set_xlsimpledataset('Train', 'data', [{'type': 'labelme_det', 'ratio': 0.9}])
        self.set_xlsimpledataset('Eval', 'data', [{'type': 'labelme_det', 'ratio': -0.1}])
        self.set_iter_num(150000)
        return self

    def det1_server(self):
        self.det1_server_init(pretrained=2)  # 基础配置
        self.set_save_dir('models/det1_server')  # 模型保存位置
        self.set_xlsimpledataset('Train', 'data', [{'type': 'labelme_det', 'ratio': 0.9}])
        self.set_xlsimpledataset('Eval', 'data', [{'type': 'labelme_det', 'ratio': -0.1}])
        self.set_iter_num(150000)
        return self


class XlRec(PaddleOcrBaseConfig):
    """ 识别模型专用配置
    """

    def rec_chinese_lite(self):
        """ 能识别中文的一个轻量模型

        """
        cfg = self.config_from_template('rec/ch_ppocr_v2.0/rec_chinese_lite_train_v2.0')
        # cfg['Global']['pretrained_model'] = Paths.models / 'ch_ppocr_mobile_v2.0_rec_train/best_accuracy'
        self.cfg['Global']['epoch_num'] = 10000
        return self


class XlCls:
    """ 分类模型 """


class XlOcr:
    """ 封装了文字技术体系，检测识别的一些标准化处理流程 """

    def __init__(self, root):
        self.root = XlPath(root)  # 项目根目录

    def step1_autolabel(self):
        """ 预标注检测、识别 """

    def step2_refinelabel(self):
        """ 人工手动优化label标注 """

    def step3_det(self):
        """ 训练检测模型 """