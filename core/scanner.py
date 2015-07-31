# Created By: Virgil Dupras
# Created On: 2006/03/03
# Copyright 2015 Hardcoded Software (http://www.hardcoded.net)
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

import logging
import re
import os.path as op

from hscommon.jobprogress import job
from hscommon.util import dedupe, rem_file_ext, get_file_ext
from hscommon.trans import tr

from . import engine
from .ignore import IgnoreList

# It's quite ugly to have scan types from all editions all put in the same class, but because there's
# there will be some nasty bugs popping up (ScanType is used in core when in should exclusively be
# used in core_*). One day I'll clean this up.

class ScanType:
    Filename = 0
    Fields = 1
    FieldsNoOrder = 2
    Tag = 3
    Folders = 4
    Contents = 5
    ContentsAudio = 6

    #PE
    FuzzyBlock = 10
    ExifTimestamp = 11

SCANNABLE_TAGS = ['track', 'artist', 'album', 'title', 'genre', 'year']

RE_DIGIT_ENDING = re.compile(r'\d+|\(\d+\)|\[\d+\]|{\d+}')

def is_same_with_digit(name, refname):
    # Returns True if name is the same as refname, but with digits (with brackets or not) at the end
    if not name.startswith(refname):
        return False
    end = name[len(refname):].strip()
    return RE_DIGIT_ENDING.match(end) is not None

def remove_dupe_paths(files):
    # Returns files with duplicates-by-path removed. Files with the exact same path are considered
    # duplicates and only the first file to have a path is kept. In certain cases, we have files
    # that have the same path, but not with the same case, that's why we normalize. However, we also
    # have case-sensitive filesystems, and in those, we don't want to falsely remove duplicates,
    # that's why we have a `samefile` mechanism.
    result = []
    path2file = {}
    for f in files:
        normalized = str(f.path).lower()
        if normalized in path2file:
            try:
                if op.samefile(normalized, str(path2file[normalized].path)):
                    continue # same file, it's a dupe
                else:
                    pass # We don't treat them as dupes
            except OSError:
                continue # File doesn't exist? Well, treat them as dupes
        else:
            path2file[normalized] = f
        result.append(f)
    return result

class Scanner:
    def __init__(self):
        self.ignore_list = IgnoreList()
        self.discarded_file_count = 0

    def _getmatches(self, files, j):
        if self.size_threshold:
            j = j.start_subjob([2, 8])
            for f in j.iter_with_progress(files, tr("Read size of %d/%d files")):
                f.size # pre-read, makes a smoother progress if read here (especially for bundles)
            files = [f for f in files if f.size >= self.size_threshold]
        if self.scan_type in {ScanType.Contents, ScanType.ContentsAudio, ScanType.Folders}:
            sizeattr = 'audiosize' if self.scan_type == ScanType.ContentsAudio else 'size'
            return engine.getmatches_by_contents(
                files, sizeattr, partial=self.scan_type == ScanType.ContentsAudio, j=j
            )
        else:
            j = j.start_subjob([2, 8])
            kw = {}
            kw['match_similar_words'] = self.match_similar_words
            kw['weight_words'] = self.word_weighting
            kw['min_match_percentage'] = self.min_match_percentage
            if self.scan_type == ScanType.FieldsNoOrder:
                self.scan_type = ScanType.Fields
                kw['no_field_order'] = True
            func = {
                ScanType.Filename: lambda f: engine.getwords(rem_file_ext(f.name)),
                ScanType.Fields: lambda f: engine.getfields(rem_file_ext(f.name)),
                ScanType.Tag: lambda f: [
                    engine.getwords(str(getattr(f, attrname)))
                    for attrname in SCANNABLE_TAGS
                    if attrname in self.scanned_tags
                ],
            }[self.scan_type]
            for f in j.iter_with_progress(files, tr("Read metadata of %d/%d files")):
                logging.debug("Reading metadata of {}".format(str(f.path)))
                f.words = func(f)
            return engine.getmatches(files, j=j, **kw)

    @staticmethod
    def _key_func(dupe):
        return -dupe.size

    @staticmethod
    def _tie_breaker(ref, dupe):
        refname = rem_file_ext(ref.name).lower()
        dupename = rem_file_ext(dupe.name).lower()
        if 'copy' in dupename:
            return False
        if 'copy' in refname:
            return True
        if is_same_with_digit(dupename, refname):
            return False
        if is_same_with_digit(refname, dupename):
            return True
        return len(dupe.path) > len(ref.path)

    def get_dupe_groups(self, files, j=job.nulljob):
        j = j.start_subjob([8, 2])
        for f in (f for f in files if not hasattr(f, 'is_ref')):
            f.is_ref = False

        # Determine if there are any reference files.
        any_reference_files = any(f.is_ref for f in files)    
            
        files = remove_dupe_paths(files)
        logging.info("Getting matches. Scan type: %d", self.scan_type)
        matches = self._getmatches(files, j)
        logging.info('Found %d matches' % len(matches))
        j.set_progress(100, tr("Removing false matches"))
        # In removing what we call here "false matches", we first want to remove, if we scan by
        # folders, we want to remove folder matches for which the parent is also in a match (they're
        # "duplicated duplicates if you will). Then, we also don't want mixed file kinds if the
        # option isn't enabled, we want matches for which both files exist and, lastly, we don't
        # want matches with both files as ref.
        if self.scan_type == ScanType.Folders and matches:
            allpath = {m.first.path for m in matches}
            allpath |= {m.second.path for m in matches}
            sortedpaths = sorted(allpath)
            toremove = set()
            last_parent_path = sortedpaths[0]
            for p in sortedpaths[1:]:
                if p in last_parent_path:
                    toremove.add(p)
                else:
                    last_parent_path = p
            matches = [m for m in matches if m.first.path not in toremove or m.second.path not in toremove]
        if not self.mix_file_kind:
            matches = [m for m in matches if get_file_ext(m.first.name) == get_file_ext(m.second.name)]
        matches = [m for m in matches if m.first.path.exists() and m.second.path.exists()]
        matches = [m for m in matches if not (m.first.is_ref and m.second.is_ref)]

        # If there are any reference files we need to exclude matches where both files are not reference.
        # But to ensure that grouping works correctly, keep match pairs that are both non-reference, if
        # there exists another match with one of the two files being matched to a reference file.
        if self.require_reference and any_reference_files:
            matches = [m for m in matches if (not (not m.first.is_ref and not m.second.is_ref)) or
            ( [a for a in matches if m.first.path == a.first.path and a.second.is_ref] or
              [b for b in matches if m.second.path == b.second.path and b.first.is_ref])
            ]

        if self.ignore_list:
            j = j.start_subjob(2)
            iter_matches = j.iter_with_progress(matches, tr("Processed %d/%d matches against the ignore list"))
            matches = [
                m for m in iter_matches
                if not self.ignore_list.AreIgnored(str(m.first.path), str(m.second.path))
            ]
        logging.info('Grouping matches')
        groups = engine.get_groups(matches, j)
        matched_files = dedupe([m.first for m in matches] + [m.second for m in matches])
        if self.scan_type in {ScanType.Filename, ScanType.Fields, ScanType.FieldsNoOrder, ScanType.Tag}:
            self.discarded_file_count = len(matched_files) - sum(len(g) for g in groups)
        else:
            # Ticket #195
            # To speed up the scan, we don't bother comparing contents of files that are both ref
            # files. However, this messes up "discarded" counting because there's a missing match
            # in cases where we end up with a dupe group anyway (with a non-ref file). Because it's
            # impossible to have discarded matches in exact dupe scans, we simply set it at 0, thus
            # bypassing our tricky problem.
            # Also, although ScanType.FuzzyBlock is not always doing exact comparisons, we also
            # bypass ref comparison, thus messing up with our "discarded" count. So we're
            # effectively disabling the "discarded" feature in PE, but it's better than falsely
            # reporting discarded matches.
            self.discarded_file_count = 0
        groups = [g for g in groups if any(not f.is_ref for f in g)]
        logging.info('Created %d groups' % len(groups))
        j.set_progress(100, tr("Doing group prioritization"))
        for g in groups:
            g.prioritize(self._key_func, self._tie_breaker)
        return groups

    match_similar_words = False
    min_match_percentage = 80
    mix_file_kind = True
    require_reference = False
    scan_type = ScanType.Filename
    scanned_tags = {'artist', 'title'}
    size_threshold = 0
    word_weighting = False

