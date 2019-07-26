#!/usr/bin/env python3

import csv
import os, sys
import glob
import argparse
import re
from neo4j import GraphDatabase, ServiceUnavailable
from icdc_schema import ICDC_Schema
from utils import *
from timeit import default_timer as timer

NODE_TYPE = 'type'

excluded_fields = { NODE_TYPE }

class Loader:
    def __init__(self, log, driver, schema, file_list):
        self.log = log
        self.driver = driver
        self.schema = schema
        self.file_list = file_list

    def load(self):
        start = timer()
        for txt in self.file_list:
            if not self.validate_file(txt):
                self.log.error('Validating file "{}" failed!'.format(txt))
                sys.exit(1)

        self.nodes_created = 0
        self.relationships_created = 0
        self.nodes_stat = {}
        self.relationships_stat = {}
        with self.driver.session() as session:
            for txt in self.file_list:
                self.load_nodes(session, txt)
            for txt in self.file_list:
                self.load_relationships(session, txt)
        end = timer()

        # Print statistics
        for node in sorted(self.nodes_stat.keys()):
            count = self.nodes_stat[node]
            self.log.info('Node: (:{}) loaded: {}'.format(node, count))
        for rel in sorted(self.relationships_stat.keys()):
            count = self.relationships_stat[rel]
            self.log.info('Relationship: [:{}] loaded: {}'.format(rel, count))
        self.log.info('{} nodes and {} relationships loaded!'.format(self.nodes_created, self.relationships_created))
        self.log.info('Loading time: {:.2f} seconds'.format(end - start))  # Time in seconds, e.g. 5.38091952400282


    def get_id_field(self, obj):
        if NODE_TYPE not in obj:
            self.log.error('get_id_field: there is no "{}" field in node, can\'t retrieve id!'.format(NODE_TYPE))
            return None
        node_type = obj[NODE_TYPE]
        if node_type:
            # TODO: put it somewhere in model to avoid hard coded special case for study
            if node_type == 'study':
                return 'clinical_study_designation'
            else:
                return node_type + '_id'
        else:
            self.log.error('get_id_field: "{}" field is empty'.format(NODE_TYPE))
            return None

    def get_id(self, obj):
        id_field = self.get_id_field(obj)
        if not id_field:
            return None
        if id_field not in obj:
            self.log.debug('get_id: there is no "{}" field in node, can\'t retrieve id!'.format(id_field))
            return None
        else:
            return obj[id_field]

    def is_valid_data(self, obj):
        if NODE_TYPE not in obj:
            return {'result': False, 'message': "{} doesn't exist!".format(NODE_TYPE)}

        # id = self.get_id(obj)
        # id_field = self.get_id_field(obj)
        # if id_field and not id:
        #     return {'result': False, 'message': "{} is empty".format(id_field)}

        return {'result': True}

    def cleanup_node(self, node):
        obj = {}
        for key, value in node.items():
            obj[key.strip()] = value.strip()
        return obj


    # Validate file
    def validate_file(self, file_name):
        with open(file_name) as in_file:
            self.log.info('Validating file "{}" ...'.format(file_name))
            reader = csv.DictReader(in_file, delimiter='\t')
            line_num = 0
            for org_obj in reader:
                obj = self.cleanup_node(org_obj)
                line_num += 1
                validate_result = self.is_valid_data(obj)
                if not validate_result['result']:
                    self.log.error('Invalid data at line {}: "{}"!'.format(line_num, validate_result['message']))
                    return False
            return True


    # load file
    def load_nodes(self, session, file_name):
        self.log.info('Loading nodes from file: {}'.format(file_name))

        with open(file_name) as in_file:
            reader = csv.DictReader(in_file, delimiter='\t')
            for org_obj in reader:
                obj = self.cleanup_node(org_obj)
                label = obj[NODE_TYPE]
                id = self.get_id(obj)
                id_field = self.get_id_field(obj)
                # statement is used to create current node
                statement = ''
                # prop_statement set properties of current node
                if id:
                    prop_statement = 'SET n.{} = "{}"'.format(id_field, id)
                else:
                    prop_statement = []

                for key, value in obj.items():
                    if key in excluded_fields:
                        continue
                    elif re.match(r'\w+\.\w+', key):
                        continue
                    elif key != id_field:
                        # log.debug('Type of {}:{} is "{}"'.format(key, value, type(value)))
                        # TODO: deal with numbers and booleans that doesn't require double quotes
                        if id:
                            prop_statement += ', n.{} = "{}"'.format(key, value)
                        else:
                            prop_statement.append('{}: "{}"'.format(key, value))

                if id:
                    statement += 'MERGE (n:{} {{{}: "{}"}})'.format(label, id_field, id)
                    statement += ' ON CREATE ' + prop_statement
                    statement += ' ON MATCH ' + prop_statement
                else:
                    statement += 'MERGE (n:{} {{ {} }})'.format(label, ', '.join(prop_statement))

                self.log.debug(statement)
                result = session.run(statement)
                count = result.summary().counters.nodes_created
                self.nodes_created += count
                self.nodes_stat[label] = self.nodes_stat.get(label, 0) + count

    def node_exists(self, session, label, property, value):
        statement = 'MATCH (m:{} {{{}: "{}"}}) return m'.format(label, property, value)
        result = session.run(statement)
        count = result.detach()
        self.log.debug('{} node(s) found'.format(count))
        if count > 1:
            self.log.warning('More than one nodes found! ')
        return count >= 1

    def load_relationships(self, session, file_name):
        self.log.info('Loading relationships from file: {}'.format(file_name))

        with open(file_name) as in_file:
            reader = csv.DictReader(in_file, delimiter='\t')
            for org_obj in reader:
                obj = self.cleanup_node(org_obj)
                label = obj[NODE_TYPE]
                id = self.get_id(obj)
                id_field = self.get_id_field(obj)
                # statement is used to create relationships between nodes
                statement = ''
                # condition_statement is used to find current node
                if id:
                    condition_statement = '{}: "{}"'.format(id_field, id)
                else:
                    condition_statement = []

                relationship = None
                for key, value in obj.items():
                    if key in excluded_fields:
                        continue
                    elif re.match(r'\w+\.\w+', key):
                        other_node, other_id = key.split('.')
                        relationship = self.schema.get_relationship(label, other_node)
                        if not relationship:
                            self.log.error('Relationship not found!')
                            sys.exit(1)
                        if not self.node_exists(session, other_node, other_id, value):
                            self.log.warning('Node (:{} {{{}: "{}"}} not found in DB!'.format(other_node, other_id, value))
                        else:
                            statement += 'MATCH (m:{} {{{}: "{}"}}) '.format(other_node, other_id, value)
                    elif not id:
                        condition_statement.append('{}: "{}"'.format(key, value))

                if statement and relationship:
                    if id:
                        statement += 'MATCH (n:{} {{ {} }}) '.format(label, condition_statement)
                    else:
                        statement += 'MATCH (n:{} {{ {} }}) '.format(label, ', '.join(condition_statement))

                    statement += 'MERGE (n)-[:{}]->(m);'.format(relationship)

                    self.log.debug(statement)
                    result = session.run(statement)
                    count = result.summary().counters.relationships_created
                    self.relationships_created += count
                    self.relationships_stat[relationship] = self.relationships_stat.get(relationship, 0) + count


def main():
    parser = argparse.ArgumentParser(description='Load TSV(TXT) files (from Pentaho) to Neo4j')
    parser.add_argument('-i', '--uri', help='Neo4j uri like bolt://12.34.56.78:7687')
    parser.add_argument('-u', '--user', help='Neo4j user')
    parser.add_argument('-p', '--password', help='Neo4j password')
    parser.add_argument('-s', '--schema', help='Schema files', action='append')
    parser.add_argument('dir', help='Data directory')

    args = parser.parse_args()

    uri = args.uri if args.uri else "bolt://localhost:7687"
    password = args.password if args.password else os.environ['NEO_PASSWORD']
    user = args.user if args.user else 'neo4j'

    log = get_logger('Data Loader')

    log.debug(args)

    try:
        file_list = glob.glob('{}/*.txt'.format(args.dir))
        schema = ICDC_Schema(args.schema)
        driver = GraphDatabase.driver(uri, auth=(user, password))
        loader = Loader(log, driver, schema, file_list)
        loader.load()

        driver.close()

    except ServiceUnavailable as err:
        log.exception(err)
        log.critical("Can't connect to Neo4j server at: \"{}\"".format(uri))

if __name__ == '__main__':
    main()
