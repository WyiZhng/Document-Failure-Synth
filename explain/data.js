var EXPLAIN = {
  "originInput": {
    "doc_id": "doc_21f4dc879ffd",
    "document_type": "policy_document",
    "reading_direction": "horizontal"
  },
  "sourceNodes": [
    {
      "id": "p0-b2",
      "category": "doc_title",
      "text": "山东省工业和信息化厅文件",
      "link_to": []
    },
    {
      "id": "p0-b3",
      "category": "text",
      "text": "鲁工信消〔2021〕77号",
      "link_to": []
    },
    {
      "id": "p0-b8",
      "category": "seal",
      "text": "",
      "link_to": []
    }
  ],
  "sourceBlocks": [
    {
      "id": "p0-b3",
      "page": 0,
      "category": "text",
      "text": "鲁工信消〔2021〕77号",
      "image_path": null
    },
    {
      "id": "p0-b8",
      "page": 0,
      "category": "seal",
      "text": "",
      "image_path": "_assets_1_52/img_p0-b8.png"
    }
  ],
  "htmlZh": "<div class=\"block\" data-category=\"text\" data-lang=\"zh\" data-node-id=\"p0-b3\">鲁工信消〔2021〕77号</div>\n<img class=\"block\" data-category=\"seal\" data-lang=\"zh\" data-node-id=\"p0-b8\" src=\"file://...\" alt=\"\" />",
  "htmlPair": "<div class=\"block\" data-category=\"text\" data-lang=\"zh\" data-node-id=\"p0-b3\">鲁工信消〔2021〕77号</div>\n<div class=\"block\" data-category=\"text\" data-lang=\"en\" data-node-id=\"p0-b3\">Lu Gongxin Xiao [2021] No. 77</div>\n<img class=\"block\" data-category=\"seal\" data-lang=\"zh\" data-node-id=\"p0-b8\" src=\"file://...\" alt=\"\" />",
  "stats": {
    "n_zh": 22,
    "n_en": 21,
    "en_cross_page": 0,
    "n_errors": 0
  },
  "idMap": [
    {
      "sourceId": "p0-b3",
      "role": "中文文号",
      "newId": "p0-b2",
      "bboxX": "40–488"
    },
    {
      "sourceId": "p0-b3",
      "role": "英文文号",
      "newId": "p0-b16",
      "bboxX": "512–960"
    }
  ],
  "artifacts": {
    "origin": {
      "doc_id": "synth_bilingual_001_doc_21f4dc879ffd",
      "task_id": "synth_task_001",
      "pdf_path": "",
      "images_path": "/home/wanyi/projects/Document_analyst_Trainer/复刻失效数据/合成数据/bilingual_v1/synth_001_doc_21f4dc879ffd/images_path",
      "reading_direction": "horizontal",
      "document_type": "policy_document"
    },
    "label": {
      "pages": [
        {
          "page_index": 0,
          "blocks": [
            {
              "block_id": 1,
              "bbox": [40.0, 40.0, 488.0, 75.1875],
              "category": "doc_title",
              "page_index": 0
            },
            {
              "block_id": 2,
              "bbox": [40.0, 83.1875, 488.0, 108.78125],
              "category": "text",
              "page_index": 0
            }
          ]
        }
      ]
    },
    "treeNode": {
      "id": "p0-b2",
      "page_index": [0, 0],
      "member": ["p0-b2", "p0-b16"],
      "category": ["text", "text"],
      "bbox": [
        [40.0, 83.1875, 488.0, 108.78125],
        [512.0, 86.0625, 960.0, 105.09375]
      ],
      "text": ["", ""]
    },
    "prelabel": {
      "block_id": 1,
      "bbox": [129.0, 43.0, 399.0, 71.0],
      "text": "山东省工业和信息化厅文件",
      "category": "doc_title",
      "score": 1.0,
      "source": "paddle_ocr"
    }
  }
};
