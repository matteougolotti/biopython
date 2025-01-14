# Copyright 2010 by Thomas Schmitt.
#
# This file is part of the Biopython distribution and governed by your
# choice of the "Biopython License Agreement" or the "BSD 3-Clause License".
# Please see the LICENSE file that should have been included as part of this
# package.
"""Bio.SeqIO support for the "seqxml" file format, SeqXML.

This module is for reading and writing SeqXML format files as
SeqRecord objects, and is expected to be used via the Bio.SeqIO API.

SeqXML is a lightweight XML format which is supposed be an alternative for
FASTA files. For more Information see http://www.seqXML.org and Schmitt et al
(2011), https://doi.org/10.1093/bib/bbr025
"""

import sys

from xml.sax.saxutils import XMLGenerator
from xml.sax.xmlreader import AttributesImpl
from xml.dom import pulldom
from xml.sax import SAXParseException

from Bio._py3k import range
from Bio._py3k import basestring
from Bio._py3k import raise_from


from Bio import Alphabet
from Bio.Seq import Seq
from Bio.Seq import UnknownSeq
from Bio.SeqRecord import SeqRecord
from .Interfaces import SequentialSequenceWriter


class SeqXmlIterator(object):
    """Breaks seqXML file into SeqRecords.

    Assumes valid seqXML please validate beforehand.
    It is assumed that all information for one record can be found within a
    record element or above. Two types of methods are called when the start
    tag of an element is reached. To receive only the attributes of an
    element before its end tag is reached implement _attr_TAGNAME.
    To get an element and its children as a DOM tree implement _elem_TAGNAME.
    Everything that is part of the DOM tree will not trigger any further
    method calls.
    """

    def __init__(self, handle, namespace=None):
        """Create the object and initialize the XML parser."""
        self.source = None
        self.source_version = None
        self.version = None
        self.speciesName = None
        self.ncbiTaxID = None
        self._namespace = namespace
        # pulldom.parse can accept both file handles and file names.
        # However, it doesn't use a context manager. so if we provide a file
        # name and let pulldom.parse open the file for us, then the file
        # will remain open until SeqXmlIterator is deallocated or we delete
        # the DOMEventStream returned by pulldom.parse.
        # Delete the DOMEventStream in case any exceptions happen.
        self._events = pulldom.parse(handle)
        try:
            try:
                event, node = next(self._events)
            except StopIteration:
                raise_from(ValueError("Empty file."), None)
            if event != "START_DOCUMENT" or node.localName is not None:
                raise ValueError("Failed to find start of XML")
            self._read_header()
        except Exception:
            self._events = None
            raise

    def _read_header(self):
        # Parse the document metadata
        event, node = next(self._events)
        if event != "START_ELEMENT" or node.localName != "seqXML":
            raise ValueError("Failed to find seqXML tag in file")
        for index in range(node.attributes.length):
            item = node.attributes.item(index)
            name = item.name
            value = item.value
            if name == "source":
                self.source = value
            elif name == "sourceVersion":
                self.sourceVersion = value
            elif name == "seqXMLversion":
                self.seqXMLversion = value
            elif name == "ncbiTaxID":
                self.ncbiTaxID = value
            elif name == "speciesName":
                self.speciesName = value

    def __iter__(self):
        return self

    def __next__(self):
        """Iterate over the records in the XML file."""
        if self._events is None:
            # No more events; we are at the end of the file
            raise StopIteration

        record = None
        try:
            for event, node in self._events:

                # the for loop is entered only if there is some content in self._events

                if event == "START_ELEMENT" and node.namespaceURI == self._namespace:

                    if node.localName == "entry":
                        # create an empty SeqRecord
                        record = SeqRecord("", id="")

                    # call matching methods with attributes only
                    if hasattr(self, "_attr_" + node.localName):
                        getattr(self, "_attr_" + node.localName)(
                            self._attributes(node), record
                        )

                    # call matching methods with DOM tree
                    if hasattr(self, "_elem_" + node.localName):
                        # read the element and all nested elements into a DOM tree
                        self._events.expandNode(node)
                        node.normalize()

                        getattr(self, "_elem_" + node.localName)(node, record)

                elif event == "END_ELEMENT":
                    if node.namespaceURI == self._namespace:
                        if node.localName == "entry":
                            return record
                        elif node.localName == "seqXML":
                            # It would be cleaner to continue reading until
                            # we get to the END_DOCUMENT event.
                            # However, pulldom seems to have a bug preventing
                            # this from happening:
                            # https://bugs.python.org/issue9371
                            # So we raise a StopIteration here.
                            # Perhaps we should switch to a SAX parser,
                            # which seems more robust.
                            # First, close any temporary file handles:
                            self._events = None
                            raise StopIteration

        except SAXParseException as e:

            # Close any temporary file handles
            self._events.clear()

            if e.getLineNumber() == 1 and e.getColumnNumber() == 0:
                # empty file
                pass
            else:
                import os

                if (
                    e.getLineNumber() == 1
                    and e.getColumnNumber() == 1
                    and os.name == "java"
                ):
                    # empty file, see http://bugs.jython.org/issue1774
                    pass
                else:
                    raise

        except Exception:

            # In case of an error, close any temporary file handles
            self._events = None
            raise

    if sys.version_info[0] < 3:  # python2
        def next(self):
            """Python 2 style alias for Python 3 style __next__ method."""
            return self.__next__()

    def _attributes(self, node):
        """Return the attributes of a DOM node as dictionary (PRIVATE)."""
        return {
            node.attributes.item(i).name: node.attributes.item(i).value
            for i in range(node.attributes.length)
        }

    def _attr_property(self, attr_dict, record):
        """Parse key value pair properties and store them as annotations (PRIVATE)."""
        if "name" not in attr_dict:
            raise ValueError("Malformed property element.")

        value = attr_dict.get("value")

        if attr_dict["name"] not in record.annotations:
            record.annotations[attr_dict["name"]] = value
        elif isinstance(record.annotations[attr_dict["name"]], list):
            record.annotations[attr_dict["name"]].append(value)
        else:
            record.annotations[attr_dict["name"]] = [
                record.annotations[attr_dict["name"]],
                value,
            ]

    def _attr_species(self, attr_dict, record):
        """Parse the species information (PRIVATE)."""
        if "name" not in attr_dict or "ncbiTaxID" not in attr_dict:
            raise ValueError("Malformed species element!")

        # the keywords for the species annotation are taken from SwissIO
        record.annotations["organism"] = attr_dict["name"]
        # TODO - Should have been a list to match SwissProt parser:
        record.annotations["ncbi_taxid"] = attr_dict["ncbiTaxID"]

    def _attr_entry(self, attr_dict, record):
        """Set new entry with id and the optional entry source (PRIVATE)."""
        if "id" not in attr_dict:
            raise ValueError("Malformed entry! Identifier is missing.")

        record.id = attr_dict["id"]
        if "source" in attr_dict:
            record.annotations["source"] = attr_dict["source"]
        elif self.source is not None:
            record.annotations["source"] = self.source

        # initialize entry with global species definition
        # the keywords for the species annotation are taken from SwissIO
        if self.ncbiTaxID is not None:
            record.annotations["ncbi_taxid"] = self.ncbiTaxID
        if self.speciesName is not None:
            record.annotations["organism"] = self.speciesName

    def _elem_DNAseq(self, node, record):
        """Parse DNA sequence (PRIVATE)."""
        if not (node.hasChildNodes() and len(node.firstChild.data) > 0):
            raise ValueError("Sequence length should be greater than 0.")

        record.seq = Seq(node.firstChild.data, Alphabet.generic_dna)

    def _elem_RNAseq(self, node, record):
        """Parse RNA sequence (PRIVATE)."""
        if not (node.hasChildNodes() and len(node.firstChild.data) > 0):
            raise ValueError("Sequence length should be greater than 0.")

        record.seq = Seq(node.firstChild.data, Alphabet.generic_rna)

    def _elem_AAseq(self, node, record):
        """Parse protein sequence (PRIVATE)."""
        if not (node.hasChildNodes() and len(node.firstChild.data) > 0):
            raise ValueError("Sequence length should be greater than 0.")

        record.seq = Seq(node.firstChild.data, Alphabet.generic_protein)

    def _elem_description(self, node, record):
        """Parse the description (PRIVATE)."""
        if node.hasChildNodes() and len(node.firstChild.data) > 0:
            record.description = node.firstChild.data

    def _attr_DBRef(self, attr_dict, record):
        """Parse a database cross reference (PRIVATE)."""
        if "source" not in attr_dict or "id" not in attr_dict:
            raise ValueError("Invalid DB cross reference.")

        if "%s:%s" % (attr_dict["source"], attr_dict["id"]) not in record.dbxrefs:
            record.dbxrefs.append("%s:%s" % (attr_dict["source"], attr_dict["id"]))


class SeqXmlWriter(SequentialSequenceWriter):
    """Writes SeqRecords into seqXML file.

    SeqXML requires the sequence alphabet be explicitly RNA, DNA or protein,
    i.e. an instance or subclass of Bio.Alphapet.RNAAlphabet,
    Bio.Alphapet.DNAAlphabet or Bio.Alphapet.ProteinAlphabet.
    """

    def __init__(
        self, handle, source=None, source_version=None, species=None, ncbiTaxId=None
    ):
        """Create Object and start the xml generator."""
        SequentialSequenceWriter.__init__(self, handle)

        self.xml_generator = XMLGenerator(handle, "utf-8")
        self.xml_generator.startDocument()
        self.source = source
        self.source_version = source_version
        self.species = species
        self.ncbiTaxId = ncbiTaxId

    def write_header(self):
        """Write root node with document metadata."""
        SequentialSequenceWriter.write_header(self)

        attrs = {
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "xsi:noNamespaceSchemaLocation": "http://www.seqxml.org/0.4/seqxml.xsd",
            "seqXMLversion": "0.4",
        }

        if self.source is not None:
            attrs["source"] = self.source
        if self.source_version is not None:
            attrs["sourceVersion"] = self.source_version
        if self.species is not None:
            if not isinstance(self.species, basestring):
                raise TypeError("species should be of type string")
            attrs["speciesName"] = self.species
        if self.ncbiTaxId is not None:
            if not isinstance(self.ncbiTaxId, (basestring, int)):
                raise TypeError("ncbiTaxID should be of type string or int")
            attrs["ncbiTaxID"] = self.ncbiTaxId

        self.xml_generator.startElement("seqXML", AttributesImpl(attrs))

    def write_record(self, record):
        """Write one record."""
        if not record.id or record.id == "<unknown id>":
            raise ValueError("SeqXML requires identifier")

        if not isinstance(record.id, basestring):
            raise TypeError("Identifier should be of type string")

        attrb = {"id": record.id}

        if (
            "source" in record.annotations
            and self.source != record.annotations["source"]
        ):
            if not isinstance(record.annotations["source"], basestring):
                raise TypeError("source should be of type string")
            attrb["source"] = record.annotations["source"]

        self.xml_generator.startElement("entry", AttributesImpl(attrb))
        self._write_species(record)
        self._write_description(record)
        self._write_seq(record)
        self._write_dbxrefs(record)
        self._write_properties(record)
        self.xml_generator.endElement("entry")

    def write_footer(self):
        """Close the root node and finish the XML document."""
        SequentialSequenceWriter.write_footer(self)

        self.xml_generator.endElement("seqXML")
        self.xml_generator.endDocument()

    def _write_species(self, record):
        """Write the species if given (PRIVATE)."""
        local_ncbi_taxid = None
        if "ncbi_taxid" in record.annotations:
            local_ncbi_taxid = record.annotations["ncbi_taxid"]
            if isinstance(local_ncbi_taxid, list):
                # SwissProt parser uses a list (which could cope with chimeras)
                if len(local_ncbi_taxid) == 1:
                    local_ncbi_taxid = local_ncbi_taxid[0]
                elif len(local_ncbi_taxid) == 0:
                    local_ncbi_taxid = None
                else:
                    ValueError(
                        'Multiple entries for record.annotations["ncbi_taxid"], %r'
                        % local_ncbi_taxid
                    )
        if "organism" in record.annotations and local_ncbi_taxid:
            local_org = record.annotations["organism"]

            if not isinstance(local_org, basestring):
                raise TypeError("organism should be of type string")

            if not isinstance(local_ncbi_taxid, (basestring, int)):
                raise TypeError("ncbiTaxID should be of type string or int")

            # The local species definition is only written if it differs from the global species definition
            if local_org != self.species or local_ncbi_taxid != self.ncbiTaxId:

                attr = {"name": local_org, "ncbiTaxID": local_ncbi_taxid}
                self.xml_generator.startElement("species", AttributesImpl(attr))
                self.xml_generator.endElement("species")

    def _write_description(self, record):
        """Write the description if given (PRIVATE)."""
        if record.description:

            if not isinstance(record.description, basestring):
                raise TypeError("Description should be of type string")

            description = record.description
            if description == "<unknown description>":
                description = ""

            if len(record.description) > 0:
                self.xml_generator.startElement("description", AttributesImpl({}))
                self.xml_generator.characters(description)
                self.xml_generator.endElement("description")

    def _write_seq(self, record):
        """Write the sequence (PRIVATE).

        Note that SeqXML requires a DNA, RNA or protein alphabet.
        """
        if isinstance(record.seq, UnknownSeq):
            raise TypeError("Sequence type is UnknownSeq but SeqXML requires sequence")

        seq = str(record.seq)

        if not len(seq) > 0:
            raise ValueError("The sequence length should be greater than 0")

        # Get the base alphabet (underneath any Gapped or StopCodon encoding)
        alpha = Alphabet._get_base_alphabet(record.seq.alphabet)
        if isinstance(alpha, Alphabet.RNAAlphabet):
            seqElem = "RNAseq"
        elif isinstance(alpha, Alphabet.DNAAlphabet):
            seqElem = "DNAseq"
        elif isinstance(alpha, Alphabet.ProteinAlphabet):
            seqElem = "AAseq"
        else:
            raise ValueError("Need a DNA, RNA or Protein alphabet")

        self.xml_generator.startElement(seqElem, AttributesImpl({}))
        self.xml_generator.characters(seq)
        self.xml_generator.endElement(seqElem)

    def _write_dbxrefs(self, record):
        """Write all database cross references (PRIVATE)."""
        if record.dbxrefs is not None:

            for dbxref in record.dbxrefs:

                if not isinstance(dbxref, basestring):
                    raise TypeError("dbxrefs should be of type list of string")
                if dbxref.find(":") < 1:
                    raise ValueError(
                        "dbxrefs should be in the form ['source:id', 'source:id' ]"
                    )

                dbsource, dbid = dbxref.split(":", 1)

                attr = {"source": dbsource, "id": dbid}
                self.xml_generator.startElement("DBRef", AttributesImpl(attr))
                self.xml_generator.endElement("DBRef")

    def _write_properties(self, record):
        """Write all annotations that are key value pairs with values of a primitive type or list of primitive types (PRIVATE)."""
        for key, value in record.annotations.items():

            if key not in ("organism", "ncbi_taxid", "source"):

                if value is None:

                    attr = {"name": key}
                    self.xml_generator.startElement("property", AttributesImpl(attr))
                    self.xml_generator.endElement("property")

                elif isinstance(value, list):

                    for v in value:
                        if isinstance(value, (int, float, basestring)):
                            attr = {"name": key, "value": v}
                            self.xml_generator.startElement(
                                "property", AttributesImpl(attr)
                            )
                            self.xml_generator.endElement("property")

                elif isinstance(value, (int, float, basestring)):

                    attr = {"name": key, "value": str(value)}
                    self.xml_generator.startElement("property", AttributesImpl(attr))
                    self.xml_generator.endElement("property")
