import sys
import os

from pyspark import SparkContext, SparkConf
from pyspark.sql import SQLContext
from pyspark.sql.types import StructField, StringType, StructType

from warcio import ArchiveIterator
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import tldextract


schema = StructType([
    StructField('parent', StringType()),
    StructField('parentTLD', StringType()),
    StructField('childTLD', StringType()),
    StructField('child', StringType())
])


def process_warcs(i_, iterator):
    # Currently, this function is processing from files in parallel across partitions.
    # We can extend this same function easily for S3

    base_dir = os.path.abspath(os.path.dirname(__file__))

    for uri in iterator:
        if uri.startswith('file:'):
            uri = uri[5:]
        uri = os.path.join(base_dir, uri)

        try:
            stream = open(uri, 'rb')
        except IOError as exception:
            continue

        for record in ArchiveIterator(stream):
            processed = process_record(record)

            if processed:
                yield processed
            continue


def process_record(record):
    if record.rec_type == 'response' and record.http_headers.get_header('Content-Type') == 'text/html':
        target_uri = record.rec_headers.get_header('WARC-Target-URI')
        html = record.content_stream().read()

        parsed = urlparse(target_uri)
        parent = parsed.scheme + '://' + parsed.netloc
        parentTLD = parsed.netloc

        return get_external_links(html, parentTLD, parent)
    else:
        return


def get_external_links(html_content, parentTLD, parent):
    """
    Extract links from the HTML
    """
    link_list = []
    unique_map = {}
    parser = BeautifulSoup(html_content, features="html.parser")

    # Find all hrefs under the 'a' html tag
    links = parser.find_all('a')

    if links:
        for link in links:
            href = link.attrs.get("href")
            # If relevant hrefs are found, store it in a list
            if href:
                href_parsed = urlparse(href)
                get_domain = href_parsed.netloc

                try:
                    parents_children = unique_map[parentTLD]
                except KeyError:
                    unique_map[parentTLD] = {}
                    parents_children = unique_map[parentTLD]

                parent_domain = tldextract.extract(parentTLD)
                child_domain = tldextract.extract(get_domain)

                if parent_domain.domain != child_domain.domain:
                    if (href.startswith("http") or href.startswith("http")) and href not in parents_children:
                    # if get_domain not in link_list and href.startswith("http"):
                        childTLD = get_domain
                        child = href

                        # print("[*] Found external link: {}".format(href))
                        link_list.append((parent, parentTLD, childTLD, child))

                        parents_children[href] = None

    return link_list


def main(input_file, output_file):
    input_data = sc.textFile(input_file)
    print('INDATA', input_data.collect())

    partition_mapped = input_data.mapPartitionsWithIndex(process_warcs)
    mapped = partition_mapped.flatMap(lambda x: x)

    df = spark.createDataFrame(mapped, schema=schema).coalesce(1).distinct()
    df.write.format("parquet").saveAsTable(output_file)

    print('OUTDATA', mapped.take(5))


if __name__ == '__main__':
    conf = SparkConf().setAll((
        ("spark.task.maxFailures", "10"),
        ("spark.locality.wait", "20s"),
        ("spark.serializer", "org.apache.spark.serializer.KryoSerializer"),
    ))

    sc = SparkContext(appName='etl', conf=conf)
    spark = SQLContext(sparkContext=sc)

    input_file = sys.argv[1]
    output_file = sys.argv[2]

    main(input_file, output_file)